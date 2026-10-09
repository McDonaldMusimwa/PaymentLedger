import argon2 from "argon2";
import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import { config } from "../config.js";
import { pool, withTransaction } from "../db.js";
import type { SigningKey } from "../keys.js";
import {
  hashToken,
  issueRefreshToken,
  signAccessToken,
  verifyAccessToken,
  type TokenUser,
} from "../tokens.js";

const REFRESH_COOKIE = "pl_refresh";

const RegisterBody = z.object({
  orgName: z.string().trim().min(1).max(200),
  email: z.email().transform((e) => e.toLowerCase()),
  password: z.string().min(10).max(200),
});

const LoginBody = z.object({
  email: z.email().transform((e) => e.toLowerCase()),
  password: z.string().min(1).max(200),
});

interface UserRow {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  role: string;
  password_hash: string;
}

const USER_SELECT = `
  SELECT u.id, u.org_id, o.name AS org_name, u.email, u.role, u.password_hash
  FROM users u JOIN organizations o ON o.id = u.org_id`;

// Verified against when the email is unknown, so response time doesn't reveal which emails exist.
const dummyHash = argon2.hash("not-a-real-password");

function parse<T>(schema: z.ZodType<T>, body: unknown, reply: FastifyReply): T | undefined {
  const result = schema.safeParse(body);
  if (!result.success) {
    reply.code(400).send({ error: "invalid_request", issues: z.flattenError(result.error).fieldErrors });
    return undefined;
  }
  return result.data;
}

function toTokenUser(row: UserRow): TokenUser {
  return { id: row.id, orgId: row.org_id, role: row.role, email: row.email };
}

function publicUser(row: UserRow) {
  return { id: row.id, email: row.email, role: row.role, orgId: row.org_id, orgName: row.org_name };
}

function setRefreshCookie(reply: FastifyReply, token: string) {
  reply.setCookie(REFRESH_COOKIE, token, {
    httpOnly: true,
    secure: config.COOKIE_SECURE,
    sameSite: "lax",
    path: "/auth",
    maxAge: config.REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60,
  });
}

async function audit(userId: string | null, action: string, req: FastifyRequest) {
  await pool.query("INSERT INTO audit_log (user_id, action, ip) VALUES ($1, $2, $3)", [userId, action, req.ip]);
}

export const authRoutes: FastifyPluginAsync<{ key: SigningKey }> = async (app, { key }) => {
  const sessionResponse = async (reply: FastifyReply, row: UserRow, refreshToken: string) => {
    setRefreshCookie(reply, refreshToken);
    return {
      accessToken: await signAccessToken(key, toTokenUser(row)),
      tokenType: "Bearer",
      expiresIn: config.ACCESS_TOKEN_TTL_SECONDS,
      user: publicUser(row),
    };
  };

  const strictLimit = { rateLimit: { max: 10, timeWindow: "1 minute" } };

  // Signing up creates a new organization (business) with the caller as its admin.
  app.post("/register", { config: strictLimit }, async (req, reply) => {
    const body = parse(RegisterBody, req.body, reply);
    if (!body) return;

    const passwordHash = await argon2.hash(body.password);
    try {
      const { row, refreshToken } = await withTransaction(async (client) => {
        const org = await client.query<{ id: string; name: string }>(
          "INSERT INTO organizations (name) VALUES ($1) RETURNING id, name",
          [body.orgName],
        );
        const user = await client.query<Omit<UserRow, "org_name">>(
          `INSERT INTO users (org_id, email, password_hash, role)
           VALUES ($1, $2, $3, 'admin')
           RETURNING id, org_id, email, role, password_hash`,
          [org.rows[0].id, body.email, passwordHash],
        );
        const row: UserRow = { ...user.rows[0], org_name: org.rows[0].name };
        return { row, refreshToken: await issueRefreshToken(client, row.id) };
      });
      await audit(row.id, "register", req);
      return reply.code(201).send(await sessionResponse(reply, row, refreshToken));
    } catch (err) {
      if ((err as { code?: string }).code === "23505") {
        return reply.code(409).send({ error: "email_taken" });
      }
      throw err;
    }
  });

  app.post("/login", { config: strictLimit }, async (req, reply) => {
    const body = parse(LoginBody, req.body, reply);
    if (!body) return;

    const { rows } = await pool.query<UserRow>(`${USER_SELECT} WHERE u.email = $1`, [body.email]);
    const row = rows[0];
    const valid = await argon2.verify(row?.password_hash ?? (await dummyHash), body.password);
    if (!row || !valid) {
      await audit(row?.id ?? null, "login_failed", req);
      return reply.code(401).send({ error: "invalid_credentials" });
    }

    await audit(row.id, "login", req);
    return sessionResponse(reply, row, await issueRefreshToken(pool, row.id));
  });

  app.post("/refresh", async (req, reply) => {
    const presented = req.cookies[REFRESH_COOKIE];
    if (!presented) return reply.code(401).send({ error: "no_refresh_token" });

    const result = await withTransaction(async (client) => {
      const { rows } = await client.query<{
        id: string;
        user_id: string;
        family_id: string;
        revoked_at: Date | null;
        expired: boolean;
      }>(
        `SELECT id, user_id, family_id, revoked_at, expires_at < now() AS expired
         FROM refresh_tokens WHERE token_hash = $1 FOR UPDATE`,
        [hashToken(presented)],
      );
      const token = rows[0];
      if (!token) return { error: "invalid_refresh_token" } as const;

      if (token.revoked_at) {
        await client.query(
          "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL",
          [token.family_id],
        );
        return { error: "refresh_token_reused", userId: token.user_id } as const;
      }
      if (token.expired) return { error: "refresh_token_expired" } as const;

      await client.query("UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1", [token.id]);
      const user = await client.query<UserRow>(`${USER_SELECT} WHERE u.id = $1`, [token.user_id]);
      return { row: user.rows[0], refreshToken: await issueRefreshToken(client, token.user_id, token.family_id) };
    });

    if ("error" in result) {
      if (result.error === "refresh_token_reused") await audit(result.userId, "refresh_token_reused", req);
      reply.clearCookie(REFRESH_COOKIE, { path: "/auth" });
      return reply.code(401).send({ error: result.error });
    }
    return sessionResponse(reply, result.row, result.refreshToken);
  });

  app.post("/logout", async (req, reply) => {
    const presented = req.cookies[REFRESH_COOKIE];
    if (presented) {
      await pool.query(
        `UPDATE refresh_tokens SET revoked_at = now()
         WHERE family_id = (SELECT family_id FROM refresh_tokens WHERE token_hash = $1)
           AND revoked_at IS NULL`,
        [hashToken(presented)],
      );
    }
    reply.clearCookie(REFRESH_COOKIE, { path: "/auth" });
    return reply.code(204).send();
  });

  app.get("/me", async (req, reply) => {
    const header = req.headers.authorization;
    if (!header?.startsWith("Bearer ")) return reply.code(401).send({ error: "missing_token" });

    let userId: string;
    try {
      userId = await verifyAccessToken(key, header.slice("Bearer ".length));
    } catch {
      return reply.code(401).send({ error: "invalid_token" });
    }

    const { rows } = await pool.query<UserRow>(`${USER_SELECT} WHERE u.id = $1`, [userId]);
    if (!rows[0]) return reply.code(401).send({ error: "invalid_token" });
    return publicUser(rows[0]);
  });
};
