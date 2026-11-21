import type { Alias } from './types';
import { decryptJson, encryptJson } from './crypto';

// Only ciphertext is stored: {v, blob}. Every operation swallows failures (private mode, quota,
// blocked upgrades) so the cache can never break the app.

const DB_NAME = 'aliasfox-cache';
const STORE = 'kv';
const RECORD = 'aliases';
const QUEUE_RECORD = 'queue';
const VERSION = 1;

function idb(): IDBFactory | null {
  try {
    return typeof indexedDB === 'undefined' || indexedDB === null ? null : indexedDB;
  } catch {
    return null;
  }
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const f = idb();
    if (f === null) {
      reject(new Error('no_idb'));
      return;
    }
    let req: IDBOpenDBRequest;
    try {
      req = f.open(DB_NAME, VERSION);
    } catch (e) {
      reject(e);
      return;
    }
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('idb_error'));
    req.onblocked = () => reject(new Error('idb_blocked'));
  });
}

function done<T>(tx: IDBTransaction, req: IDBRequest<T> | null): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve(req === null ? undefined : req.result);
    tx.onerror = () => reject(tx.error ?? new Error('idb_error'));
    tx.onabort = () => reject(tx.error ?? new Error('idb_abort'));
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => IDBRequest<T> | null
): Promise<T | undefined> {
  const db = await openDb();
  try {
    const tx = db.transaction(STORE, mode);
    const p = done(tx, fn(tx.objectStore(STORE)));
    return await p;
  } finally {
    db.close();
  }
}

async function deleteRecord(record = RECORD): Promise<void> {
  try {
    await withStore('readwrite', (s) => s.delete(record));
  } catch {
    // ignore
  }
}

// `record` picks the stored list: the aliases (default) or the offline change queue.
async function load<T = Alias>(key: CryptoKey, record = RECORD): Promise<T[] | null> {
  try {
    if (idb() === null) return null;
    const rec = await withStore('readonly', (s) => s.get(record));
    if (rec === undefined) return null;
    const r = rec as { v?: unknown; blob?: unknown } | null;
    if (r === null || typeof r !== 'object' || r.v !== 1 || !ArrayBuffer.isView(r.blob)) {
      await deleteRecord(record);
      return null;
    }
    try {
      const list = await decryptJson<unknown>(key, r.blob as Uint8Array);
      if (!Array.isArray(list)) throw new Error('bad_payload');
      return list as T[];
    } catch {
      await deleteRecord(record);
      return null;
    }
  } catch {
    return null;
  }
}

// Bumped synchronously at the start of clear(); a save that began earlier aborts silently, so a
// logout can never be followed by a late write that resurrects the cache.
let cacheEpoch = 0;

async function save<T = Alias>(key: CryptoKey, items: T[], record = RECORD): Promise<void> {
  const epoch = cacheEpoch;
  let db: IDBDatabase | null = null;
  try {
    if (idb() === null) return;
    const blob = await encryptJson(key, items);
    if (epoch !== cacheEpoch) return;
    db = await openDb();
    if (epoch !== cacheEpoch) return;
    const tx = db.transaction(STORE, 'readwrite');
    const finished = done(tx, null);
    // No await between the last epoch check and put().
    tx.objectStore(STORE).put({ v: 1, blob }, record);
    if (epoch !== cacheEpoch) tx.abort();
    await finished;
  } catch {
    // quota / unavailable / aborted: the cache is best effort
  } finally {
    db?.close();
  }
}

async function clear(): Promise<void> {
  cacheEpoch++;
  const f = idb();
  if (f === null) return;
  await deleteRecord();
  try {
    await new Promise<void>((resolve) => {
      const req = f.deleteDatabase(DB_NAME);
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
      req.onblocked = () => resolve();
    });
  } catch {
    // best effort
  }
}

export const cache = {
  load,
  save,
  clear,
  loadQueue: <T>(key: CryptoKey) => load<T>(key, QUEUE_RECORD),
  saveQueue: <T>(key: CryptoKey, ops: T[]) => save<T>(key, ops, QUEUE_RECORD)
};
