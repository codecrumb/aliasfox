// AES-GCM helpers for the browser cache. The raw key bytes only exist inside importCacheKey;
// the resulting CryptoKey is non-extractable.

const AAD = new TextEncoder().encode('aliasfox-cache-v1');
const IV_BYTES = 12;
const KEY_BYTES = 32;
const B64URL_32 = /^[A-Za-z0-9_-]{43}$/;

export class CacheCryptoError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CacheCryptoError';
  }
}

function decodeKey(b64url: string): Uint8Array<ArrayBuffer> {
  if (typeof b64url !== 'string' || !B64URL_32.test(b64url)) {
    throw new CacheCryptoError('invalid_cache_key');
  }
  const bin = atob(b64url.replace(/-/g, '+').replace(/_/g, '/') + '=');
  if (bin.length !== KEY_BYTES) throw new CacheCryptoError('invalid_cache_key');
  const out = new Uint8Array(KEY_BYTES);
  for (let i = 0; i < KEY_BYTES; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function importCacheKey(b64url: string): Promise<CryptoKey> {
  const raw = decodeKey(b64url);
  try {
    return await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
  } finally {
    raw.fill(0);
  }
}

export async function encryptJson(key: CryptoKey, value: unknown): Promise<Uint8Array> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const plain = new TextEncoder().encode(JSON.stringify(value));
  const ct = new Uint8Array(
    await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: AAD }, key, plain)
  );
  const out = new Uint8Array(IV_BYTES + ct.length);
  out.set(iv, 0);
  out.set(ct, IV_BYTES);
  return out;
}

export async function decryptJson<T>(key: CryptoKey, blob: Uint8Array): Promise<T> {
  // AES-GCM needs at least the 16-byte tag after the IV.
  if (!(blob instanceof Uint8Array) && !ArrayBuffer.isView(blob)) throw new CacheCryptoError('bad_blob');
  const bytes = new Uint8Array(blob.buffer, blob.byteOffset, blob.byteLength);
  if (bytes.length < IV_BYTES + 16) throw new CacheCryptoError('bad_blob');
  const iv = bytes.slice(0, IV_BYTES);
  const ct = bytes.slice(IV_BYTES);
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv, additionalData: AAD }, key, ct);
  return JSON.parse(new TextDecoder().decode(plain)) as T;
}
