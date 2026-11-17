import uFuzzy from '@leeoniya/ufuzzy';
import type { Alias } from './types';

// Fuzzy search over a per-alias haystack string. The needle is only ever treated as text:
// uFuzzy escapes terms, and anything it cannot express (non-Latin text, punctuation-only input,
// quotes/exclusions syntax, many terms) falls back to a literal substring scan.

export interface Index {
  aliases: Alias[];
  haystack: string[];
  newestFirst: Alias[];
  fuzzy: uFuzzy;
}

const SEP = '\u0001';
const MAX_NEEDLE = 200;
const MAX_FUZZY_TERMS = 4;
const MAX_FUZZY_NEEDLE = 48;
const INFO_THRESH = 1000;

export function normalize(s: string): string {
  return s.normalize('NFKD').replace(/\p{M}+/gu, '').toLowerCase();
}

function haystackOf(a: Alias): string {
  return normalize(
    [a.label, a.hme, a.note, a.domain, a.forward_to, a.origin, a.origin_app_name]
      .map((v) => (typeof v === 'string' ? v : ''))
      .join(SEP)
  );
}

function byNewest(a: Alias, b: Alias): number {
  const x = a.created_ms;
  const y = b.created_ms;
  if (x === y) return 0;
  if (x === null || x === undefined) return 1;
  if (y === null || y === undefined) return -1;
  return y - x;
}

export function buildIndex(aliases: Alias[]): Index {
  return {
    aliases,
    haystack: aliases.map(haystackOf),
    // Array.prototype.sort is stable: equal timestamps keep their input order.
    newestFirst: aliases.slice().sort(byNewest),
    fuzzy: new uFuzzy({ intraMode: 1, intraSub: 1, intraTrn: 1, intraDel: 1, interLft: 0, interRgt: 0 })
  };
}

function scan(index: Index, terms: string[]): Alias[] {
  const out: Alias[] = [];
  const { haystack, aliases } = index;
  for (let i = 0; i < haystack.length; i++) {
    const h = haystack[i] ?? '';
    let ok = true;
    for (const t of terms) {
      if (!h.includes(t)) {
        ok = false;
        break;
      }
    }
    const a = aliases[i];
    if (ok && a !== undefined) out.push(a);
  }
  return out;
}

const FUZZY_SAFE = /^[\x20-\x7e]+$/;

function fuzzyEligible(needle: string, terms: string[]): boolean {
  if (!FUZZY_SAFE.test(needle) || !/[a-z0-9]/.test(needle)) return false;
  if (needle.length > MAX_FUZZY_NEEDLE || terms.length > MAX_FUZZY_TERMS || needle.includes('"')) return false;
  return !terms.some((t) => t.startsWith('-') || t.startsWith('^') || t.startsWith('!'));
}

function pick(index: Index, idxs: number[]): Alias[] {
  const out: Alias[] = [];
  for (const i of idxs) {
    const a = index.aliases[i];
    if (a !== undefined) out.push(a);
  }
  return out;
}

// GitHub-style qualifiers: `is:off`, `site:amazon`, `label:"gift card"`, `-app:spotify`, `sort:oldest`.
// Anything else (including an unknown `key:`) stays ordinary search text.
export type SortMode = 'newest' | 'oldest' | 'label' | 'address';
type QualKey = 'is' | 'label' | 'note' | 'site' | 'app' | 'to' | 'from' | 'sort';
export interface Qual {
  key: Exclude<QualKey, 'sort'>;
  value: string;
  negate: boolean;
}
export interface Parsed {
  free: string;
  quals: Qual[];
  sort: SortMode | null;
}

const QUAL_KEYS: Record<string, QualKey> = {
  is: 'is',
  label: 'label',
  note: 'note',
  site: 'site',
  domain: 'site',
  app: 'app',
  to: 'to',
  forward: 'to',
  from: 'from',
  origin: 'from',
  sort: 'sort'
};
const SORTS: Record<string, SortMode> = {
  new: 'newest',
  newest: 'newest',
  old: 'oldest',
  oldest: 'oldest',
  label: 'label',
  name: 'label',
  address: 'address',
  email: 'address'
};
const ORIGIN_NAMES: Record<string, string> = { SAFARI: 'safari', MAIL: 'mail', IN_APP: 'app', ON_DEMAND: 'on demand' };
const QUAL_RE = /(^|\s)(-?)([a-z]+):(?:"([^"]*)"|(\S*))/gi;
const MAX_PARSE = 1000;

export function parseQuery(text: string): Parsed {
  const quals: Qual[] = [];
  let sort: SortMode | null = null;
  const free = String(text)
    .slice(0, MAX_PARSE)
    .replace(QUAL_RE, (whole, lead: string, neg: string, k: string, quoted?: string, bare?: string) => {
      const key = Object.hasOwn(QUAL_KEYS, k.toLowerCase()) ? QUAL_KEYS[k.toLowerCase()] : undefined;
      if (key === undefined) return whole;
      const value = normalize(quoted ?? bare ?? '').trim();
      if (key === 'sort') {
        if (Object.hasOwn(SORTS, value)) sort = SORTS[value] ?? null;
      } else if (value !== '') {
        quals.push({ key, value, negate: neg === '-' });
      }
      return lead;
    });
  return { free, quals, sort };
}

function has(v: string | null | undefined, needle: string): boolean {
  return typeof v === 'string' && normalize(v).includes(needle);
}

function matchesQual(a: Alias, q: Qual): boolean {
  let hit: boolean;
  switch (q.key) {
    case 'is':
      hit = ['active', 'on'].includes(q.value) ? a.is_active : ['inactive', 'off'].includes(q.value) ? !a.is_active : false;
      break;
    case 'label':
      hit = has(a.label, q.value);
      break;
    case 'note':
      hit = has(a.note, q.value);
      break;
    case 'site':
      hit = has(a.domain, q.value);
      break;
    case 'app':
      hit = has(a.origin_app_name, q.value);
      break;
    case 'to':
      hit = has(a.forward_to, q.value);
      break;
    case 'from':
      hit = has(a.origin, q.value) || has(a.origin === null ? null : ORIGIN_NAMES[a.origin], q.value);
      break;
  }
  return hit !== q.negate;
}

const collator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });

/** Orders a result list. 'newest' keeps the incoming order (newest first, or best match first). */
export function sortAliases(list: Alias[], mode: SortMode): Alias[] {
  if (mode === 'newest') return list;
  const out = list.slice();
  if (mode === 'oldest') {
    return out.sort((a, b) => {
      const x = a.created_ms ?? null;
      const y = b.created_ms ?? null;
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return x - y;
    });
  }
  const key = (a: Alias): string => (mode === 'label' ? (a.label ?? '').trim() : a.hme);
  return out.sort((a, b) => {
    const x = key(a);
    const y = key(b);
    if (x === '' || y === '') return x === y ? 0 : x === '' ? 1 : -1;
    return collator.compare(x, y);
  });
}

export function query(index: Index, text: string): Alias[] {
  const { free, quals } = parseQuery(text);
  const base = queryText(index, free);
  return quals.length === 0 ? base : base.filter((a) => quals.every((q) => matchesQual(a, q)));
}

function queryText(index: Index, text: string): Alias[] {
  const raw = normalize(String(text)).slice(0, MAX_NEEDLE);
  if (raw.trim() === '') return index.newestFirst.slice();
  // Whitespace controls separate terms; any other control char (notably the haystack separator
  // \u0001) is dropped so it can never match across fields.
  const needle = raw.replace(/[\t\n\v\f\r]/g, ' ').replace(/\p{Cc}/gu, '').trim();
  if (needle === '') return [];
  const terms = needle.split(/\s+/);
  if (Array.from(needle).length === 1 || !fuzzyEligible(needle, terms)) return scan(index, terms);
  try {
    const [idxs, info, order] = index.fuzzy.search(index.haystack, needle, MAX_FUZZY_TERMS, INFO_THRESH);
    if (idxs === null) return scan(index, terms);
    if (info !== null && order !== null) return pick(index, order.map((o) => info.idx[o] ?? -1));
    return pick(index, idxs);
  } catch {
    return scan(index, terms);
  }
}
