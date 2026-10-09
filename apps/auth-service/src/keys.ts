import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  calculateJwkThumbprint,
  exportJWK,
  exportPKCS8,
  generateKeyPair,
  importJWK,
  importPKCS8,
  type JWK,
} from "jose";
import { config } from "./config.js";

const ALG = "EdDSA";

type Key = Awaited<ReturnType<typeof importPKCS8>>;

export interface SigningKey {
  alg: string;
  kid: string;
  privateKey: Key;
  publicKey: Awaited<ReturnType<typeof importJWK>>;
  publicJwk: JWK;
}

async function loadPem(): Promise<string> {
  if (config.JWT_PRIVATE_KEY) return config.JWT_PRIVATE_KEY.replace(/\\n/g, "\n");

  const file = path.join(config.KEYS_DIR, "jwt-private.pem");
  try {
    return await readFile(file, "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }

  const { privateKey } = await generateKeyPair(ALG, { extractable: true });
  const pem = await exportPKCS8(privateKey);
  await mkdir(config.KEYS_DIR, { recursive: true });
  await writeFile(file, pem, { mode: 0o600 });
  return pem;
}

export async function loadSigningKey(): Promise<SigningKey> {
  const privateKey = await importPKCS8(await loadPem(), ALG, { extractable: true });
  const { d: _private, ...jwk } = await exportJWK(privateKey);
  const kid = await calculateJwkThumbprint(jwk);
  const publicJwk: JWK = { ...jwk, kid, alg: ALG, use: "sig" };
  return { alg: ALG, kid, privateKey, publicKey: await importJWK(publicJwk, ALG), publicJwk };
}
