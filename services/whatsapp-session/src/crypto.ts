import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

export type AuthBinding = Readonly<{ workspace_id: string; connector_id: string; key_type: string; key_id: string }>;
function aad(binding: AuthBinding): Buffer {
  return Buffer.from(JSON.stringify([binding.workspace_id, binding.connector_id, binding.key_type, binding.key_id]));
}
export function encryptionKey(encoded: string): Buffer {
  if (!/^[A-Za-z0-9+/]{43}=$/.test(encoded)) throw new Error("SESSION_ENCRYPTION_KEY must be a base64 32-byte key");
  const key = Buffer.from(encoded, "base64");
  if (key.length !== 32 || key.toString("base64") !== encoded) throw new Error("Invalid session encryption key");
  return key;
}
export function encryptAuth(key: Buffer, binding: AuthBinding, plaintext: string): string {
  if (key.length !== 32 || Buffer.byteLength(plaintext) > 1_048_576) throw new Error("Invalid auth value");
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(aad(binding));
  const body = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return JSON.stringify({ v: 1, iv: iv.toString("base64"), tag: cipher.getAuthTag().toString("base64"),
    body: body.toString("base64") });
}
export function decryptAuth(key: Buffer, binding: AuthBinding, ciphertext: string): string {
  if (key.length !== 32 || Buffer.byteLength(ciphertext) > 1_500_000) throw new Error("Invalid encrypted auth value");
  const value = JSON.parse(ciphertext) as Record<string, unknown>;
  if (Object.keys(value).sort().join() !== "body,iv,tag,v" || value.v !== 1 ||
      typeof value.iv !== "string" || typeof value.tag !== "string" || typeof value.body !== "string") {
    throw new Error("Invalid encrypted auth envelope");
  }
  const iv = Buffer.from(value.iv, "base64"), tag = Buffer.from(value.tag, "base64");
  if (iv.length !== 12 || tag.length !== 16) throw new Error("Invalid encrypted auth envelope");
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAAD(aad(binding));
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(Buffer.from(value.body, "base64")), decipher.final()]).toString("utf8");
}
