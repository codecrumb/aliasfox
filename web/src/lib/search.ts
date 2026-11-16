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
  if (terms.length > MAX_FUZZY_TERMS || needle.includes('"')) return false;
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

export function query(index: Index, text: string): Alias[] {
  const needle = normalize(String(text)).slice(0, MAX_NEEDLE).trim();
  if (needle === '') return index.newestFirst.slice();
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
