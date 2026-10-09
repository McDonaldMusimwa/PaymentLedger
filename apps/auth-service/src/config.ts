import { z } from "zod";

const Env = z.object({
  PORT: z.coerce.number().default(4000),
  DATABASE_URL: z.string().default("postgres://auth:auth@localhost:5432/auth_db"),
  JWT_ISSUER: z.string().default("paymentledger-auth"),
  JWT_AUDIENCE: z.string().default("paymentledger-api"),
  ACCESS_TOKEN_TTL_SECONDS: z.coerce.number().default(900),
  REFRESH_TOKEN_TTL_DAYS: z.coerce.number().default(30),
  // PEM-encoded Ed25519 PKCS#8 key. If unset, a key is generated once and kept in KEYS_DIR.
  JWT_PRIVATE_KEY: z.string().optional(),
  KEYS_DIR: z.string().default("./keys"),
  CORS_ORIGINS: z.string().default("http://localhost:5173"),
  COOKIE_SECURE: z
    .enum(["true", "false"])
    .default("false")
    .transform((v) => v === "true"),
});

export const config = Env.parse(process.env);
