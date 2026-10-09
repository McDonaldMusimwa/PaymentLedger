import cookie from "@fastify/cookie";
import cors from "@fastify/cors";
import rateLimit from "@fastify/rate-limit";
import Fastify from "fastify";
import { config } from "./config.js";
import { pool } from "./db.js";
import { loadSigningKey } from "./keys.js";
import { migrate } from "./migrate.js";
import { authRoutes } from "./routes/auth.js";

const app = Fastify({ logger: true, trustProxy: true });

await app.register(cors, {
  origin: config.CORS_ORIGINS.split(",").map((o) => o.trim()),
  credentials: true,
});
await app.register(cookie);
await app.register(rateLimit, { global: false });

await migrate((msg) => app.log.info(msg));
const key = await loadSigningKey();
app.log.info({ kid: key.kid }, "signing key loaded");

app.get("/health", async () => {
  await pool.query("SELECT 1");
  return { status: "ok" };
});

// Other services (ledger-api) verify access tokens against this public key set.
app.get("/.well-known/jwks.json", async (_req, reply) => {
  reply.header("cache-control", "public, max-age=300");
  return { keys: [key.publicJwk] };
});

await app.register(authRoutes, { prefix: "/auth", key });

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, async () => {
    await app.close();
    await pool.end();
    process.exit(0);
  });
}

await app.listen({ host: "0.0.0.0", port: config.PORT });
