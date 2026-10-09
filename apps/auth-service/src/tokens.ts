import { createHash, randomBytes, randomUUID } from "node:crypto";
import { jwtVerify, SignJWT } from "jose";
import type pg from "pg";
import { config } from "./config.js";
import type { SigningKey } from "./keys.js";

export interface TokenUser {
  id: string;
  orgId: string;
  role: string;
  email: string;
}

export function signAccessToken(key: SigningKey, user: TokenUser): Promise<string> {
  return new SignJWT({ org_id: user.orgId, role: user.role, email: user.email })
    .setProtectedHeader({ alg: key.alg, kid: key.kid, typ: "JWT" })
    .setSubject(user.id)
    .setIssuer(config.JWT_ISSUER)
    .setAudience(config.JWT_AUDIENCE)
    .setIssuedAt()
    .setExpirationTime(`${config.ACCESS_TOKEN_TTL_SECONDS}s`)
    .setJti(randomUUID())
    .sign(key.privateKey);
}

export async function verifyAccessToken(key: SigningKey, token: string): Promise<string> {
  const { payload } = await jwtVerify(token, key.publicKey, {
    issuer: config.JWT_ISSUER,
    audience: config.JWT_AUDIENCE,
    algorithms: [key.alg],
  });
  if (!payload.sub) throw new Error("token has no subject");
  return payload.sub;
}

export const hashToken = (token: string) => createHash("sha256").update(token).digest("hex");

export async function issueRefreshToken(
  db: pg.ClientBase | pg.Pool,
  userId: string,
  familyId: string = randomUUID(),
): Promise<string> {
  const token = randomBytes(32).toString("base64url");
  await db.query(
    `INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)
     VALUES ($1, $2, $3, now() + make_interval(days => $4))`,
    [userId, familyId, hashToken(token), config.REFRESH_TOKEN_TTL_DAYS],
  );
  return token;
}
