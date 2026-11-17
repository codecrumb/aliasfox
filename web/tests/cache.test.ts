import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Alias } from '../src/lib/types';
import { decryptJson, encryptJson, importCacheKey } from '../src/lib/crypto';
import { cache } from '../src/lib/cache';

const hold = vi.hoisted(() => ({ encrypt: null as Promise<void> | null }));
vi.mock('../src/lib/crypto', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/crypto')>('../src/lib/crypto');
  return {
    ...actual,
    encryptJson: async (k: CryptoKey, v: unknown) => {
      const gate = hold.encrypt;
      const r = await actual.encryptJson(k, v);
      if (gate) await gate;
      return r;
    }
  };
});
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await tick();
}
async function cacheDbExists(): Promise<boolean> {
  return (await indexedDB.databases()).some((d) => d.name === 'aliasfox-cache');
}

function b64url(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
const KEY_A = b64url(new Uint8Array(32).fill(7));
const KEY_B = b64url(new Uint8Array(32).fill(9));

function alias(n: number, over: Partial<Alias> = {}): Alias {
  return {
    anonymous_id: `id${n}`,
    hme: `secret.fox${n}@icloud.com`,
    label: `Shopping Label${n}`,
    note: null,
    is_active: true,
    forward_to: null,
    origin: null,
    domain: null,
    created_ms: n,
    origin_app_name: null,
    app_bundle_id: null,
    ...over
  };
}

function rawRecord(): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('aliasfox-cache', 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const req = db.transaction('kv', 'readonly').objectStore('kv').get('aliases');
      req.onsuccess = () => {
        db.close();
        resolve(req.result);
      };
      req.onerror = () => reject(req.error);
    };
  });
}

function putRaw(value: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('aliasfox-cache', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('kv');
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction('kv', 'readwrite');
      tx.objectStore('kv').put(value, 'aliases');
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => reject(tx.error);
    };
    open.onerror = () => reject(open.error);
  });
}

beforeEach(async () => {
  await cache.clear();
});
afterEach(() => {
  hold.encrypt = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('crypto', () => {
  it('test_encrypt_decrypt_roundtrip_and_random_iv', async () => {
    const key = await importCacheKey(KEY_A);
    const v = { a: [1, 'é', null], b: 'x' };
    const b1 = await encryptJson(key, v);
    const b2 = await encryptJson(key, v);
    expect(await decryptJson(key, b1)).toEqual(v);
    expect(b1.length).toBe(b2.length);
    expect(Array.from(b1.slice(0, 12))).not.toEqual(Array.from(b2.slice(0, 12)));
    expect(Array.from(b1)).not.toEqual(Array.from(b2));
  });

  it('imports a non-extractable key', async () => {
    const key = await importCacheKey(KEY_A);
    expect(key.extractable).toBe(false);
    await expect(crypto.subtle.exportKey('raw', key)).rejects.toBeTruthy();
  });

  it('rejects wrong length and non-base64url keys', async () => {
    await expect(importCacheKey(b64url(new Uint8Array(31)))).rejects.toThrow();
    await expect(importCacheKey(b64url(new Uint8Array(33)))).rejects.toThrow();
    await expect(importCacheKey('')).rejects.toThrow();
    await expect(importCacheKey(KEY_A + '=')).rejects.toThrow();
    await expect(importCacheKey(KEY_A.slice(0, 10) + '+' + KEY_A.slice(11))).rejects.toThrow();
    await expect(importCacheKey('!'.repeat(43))).rejects.toThrow();
  });

  it('decryptJson throws on short blob, wrong key, tamper and AAD mismatch', async () => {
    const a = await importCacheKey(KEY_A);
    const b = await importCacheKey(KEY_B);
    const blob = await encryptJson(a, { x: 1 });
    await expect(decryptJson(a, new Uint8Array(5))).rejects.toThrow();
    await expect(decryptJson(b, blob)).rejects.toThrow();
    const t = blob.slice();
    t[t.length - 20] = (t[t.length - 20] ?? 0) ^ 1;
    await expect(decryptJson(a, t)).rejects.toThrow();
    // same key, different AAD
    const raw = await crypto.subtle.importKey('raw', new Uint8Array(32).fill(7), 'AES-GCM', false, [
      'encrypt'
    ]);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(
      await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode('other') },
        raw,
        new TextEncoder().encode('{"x":1}')
      )
    );
    const bad = new Uint8Array(12 + ct.length);
    bad.set(iv);
    bad.set(ct, 12);
    await expect(decryptJson(a, bad)).rejects.toThrow();
  });
});

describe('cache', () => {
  it('test_cache_roundtrip_in_fake_indexeddb_and_blob_has_no_plaintext_hme', async () => {
    const key = await importCacheKey(KEY_A);
    const list = [alias(1), alias(2, { note: 'visible-note-text', domain: 'example.org' })];
    await cache.save(key, list);
    expect(await cache.load(key)).toEqual(list);
    const rec = (await rawRecord()) as { v: number; blob: Uint8Array };
    expect(Object.keys(rec).sort()).toEqual(['blob', 'v']);
    expect(rec.v).toBe(1);
    const bytes = new Uint8Array(rec.blob);
    const text = new TextDecoder('latin1').decode(bytes);
    for (const s of ['secret.fox', 'icloud.com', 'Shopping', 'visible-note', 'example.org', 'id1'])
      expect(text).not.toContain(s);
  });

  it('test_wrong_key_returns_null_not_throw', async () => {
    const a = await importCacheKey(KEY_A);
    const b = await importCacheKey(KEY_B);
    await cache.save(a, [alias(1)]);
    expect(await cache.load(b)).toBeNull();
    // undecryptable record is deleted silently
    expect(await rawRecord()).toBeUndefined();
    expect(await cache.load(a)).toBeNull();
  });

  it('test_corrupt_blob_returns_null', async () => {
    const key = await importCacheKey(KEY_A);
    await cache.save(key, [alias(1)]);
    const rec = (await rawRecord()) as { v: number; blob: Uint8Array };
    const blob = new Uint8Array(rec.blob);
    blob[blob.length - 5] = (blob[blob.length - 5] ?? 0) ^ 0xff;
    await putRaw({ v: 1, blob });
    expect(await cache.load(key)).toBeNull();
    expect(await rawRecord()).toBeUndefined();
    await putRaw('garbage');
    expect(await cache.load(key)).toBeNull();
    await putRaw({ v: 1, blob: 'nope' });
    expect(await cache.load(key)).toBeNull();
  });

  it('wrong version and non-array payloads return null', async () => {
    const key = await importCacheKey(KEY_A);
    await putRaw({ v: 2, blob: await encryptJson(key, []) });
    expect(await cache.load(key)).toBeNull();
    await putRaw({ v: 1, blob: await encryptJson(key, { not: 'array' }) });
    expect(await cache.load(key)).toBeNull();
  });

  it('test_clear_removes_everything', async () => {
    const key = await importCacheKey(KEY_A);
    await cache.save(key, [alias(1)]);
    await cache.clear();
    const dbs = await indexedDB.databases();
    expect(dbs.some((d) => d.name === 'aliasfox-cache')).toBe(false);
    expect(await cache.load(key)).toBeNull();
  });

  it('is a no-op when indexedDB is undefined', async () => {
    const key = await importCacheKey(KEY_A);
    vi.stubGlobal('indexedDB', undefined);
    await expect(cache.save(key, [alias(1)])).resolves.toBeUndefined();
    await expect(cache.load(key)).resolves.toBeNull();
    await expect(cache.clear()).resolves.toBeUndefined();
  });

  it('never throws when indexedDB.open throws', async () => {
    const key = await importCacheKey(KEY_A);
    vi.stubGlobal('indexedDB', {
      open: () => {
        throw new Error('blocked');
      }
    });
    await expect(cache.save(key, [alias(1)])).resolves.toBeUndefined();
    await expect(cache.load(key)).resolves.toBeNull();
    await expect(cache.clear()).resolves.toBeUndefined();
  });

  it('a save held in encryptJson across clear() cannot resurrect the cache', async () => {
    const key = await importCacheKey(KEY_A);
    let release!: () => void;
    hold.encrypt = new Promise<void>((r) => (release = r));
    const p = cache.save(key, Array.from({ length: 500 }, (_, i) => alias(i)));
    await tick();
    await cache.clear();
    hold.encrypt = null;
    release();
    await p;
    await settle();
    expect(await cacheDbExists()).toBe(false);
    expect(await cache.load(key)).toBeNull();
  });

  it('a save held at openDb across clear() cannot resurrect the cache', async () => {
    const key = await importCacheKey(KEY_A);
    const real = indexedDB;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let first = true;
    let held = false;
    vi.stubGlobal('indexedDB', {
      open: (name: string, v?: number) => {
        const req = real.open(name, v);
        if (!first) return req;
        first = false;
        held = true;
        return new Proxy(req, {
          set(t, prop, val) {
            if (prop === 'onsuccess') {
              t.onsuccess = function (this: IDBRequest<IDBDatabase>, ev: Event) {
                void gate.then(() => (val as (e: Event) => void).call(this, ev));
              };
              return true;
            }
            return Reflect.set(t, prop, val);
          },
          get: (t, prop) => Reflect.get(t, prop, t)
        });
      },
      deleteDatabase: (n: string) => real.deleteDatabase(n),
      databases: () => real.databases()
    });
    const p = cache.save(key, [alias(1)]);
    // Wait until save() has really reached openDb (encryption is asynchronous and can take longer
    // than one macrotask under load); otherwise clear()'s own open would be the one held, and
    // clear() would wait for a gate only this test can release.
    for (let i = 0; i < 2000 && !held; i++) await tick();
    expect(held).toBe(true);
    await cache.clear();
    release();
    await p;
    await settle();
    vi.unstubAllGlobals();
    expect(await cacheDbExists()).toBe(false);
    expect(await cache.load(key)).toBeNull();
  });
});
