import type { Alias } from './types';

// CSV export of the alias list, and a CSV import that only changes labels and notes of aliases
// that already exist (matched by address). Nothing here talks to Apple.

const LABEL_MAX = 200;
const NOTE_MAX = 2000;
export const IMPORT_MAX_BYTES = 2_000_000;

const COLUMNS = ['address', 'label', 'note', 'active', 'forward_to', 'created'] as const;

// A cell starting with one of these is run as a formula by spreadsheets, so it is prefixed with an
// apostrophe on export and the prefix is removed again on import.
const FORMULA = /^[=+\-@\t\r]/;

function cell(v: string): string {
  const s = FORMULA.test(v) ? `'${v}` : v;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(list: Alias[]): string {
  const rows = list.map((a) => [
    a.hme,
    a.label ?? '',
    a.note ?? '',
    a.is_active ? 'yes' : 'no',
    a.forward_to ?? '',
    typeof a.created_ms === 'number' && a.created_ms > 0 ? new Date(a.created_ms).toISOString() : ''
  ]);
  return [COLUMNS as readonly string[], ...rows].map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
}

/** RFC 4180 fields: quotes, doubled quotes, newlines inside quotes. A leading BOM is ignored. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  for (let i = 0; i < src.length; i++) {
    const c = src[i]!;
    if (quoted) {
      if (c !== '"') field += c;
      else if (src[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = false;
    } else if (c === '"') quoted = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== '' || row.length > 0) rows.push([...row, field]);
  return rows.filter((r) => r.some((f) => f.trim() !== ''));
}

export interface ImportPlan {
  updates: { alias: Alias; label: string; note: string }[];
  unknown: number;
  inactive: number;
  unchanged: number;
}

const unformula = (v: string): string => (/^'[=+\-@\t\r]/.test(v) ? v.slice(1) : v);

/** Throws an Error with a user-facing message when the file isn't a usable CSV. */
export function planImport(text: string, list: Alias[]): ImportPlan {
  const rows = parseCsv(text);
  const head = (rows[0] ?? []).map((h) => h.trim().toLowerCase());
  const iAddr = head.indexOf('address');
  const iLabel = head.indexOf('label');
  const iNote = head.indexOf('note');
  if (iAddr < 0 || (iLabel < 0 && iNote < 0)) {
    throw new Error('Expected a CSV with an "address" column and a "label" and/or "note" column.');
  }
  const byAddress = new Map(list.map((a) => [a.hme.toLowerCase(), a]));
  const plan: ImportPlan = { updates: [], unknown: 0, inactive: 0, unchanged: 0 };
  const seen = new Set<string>();
  for (const r of rows.slice(1)) {
    const a = byAddress.get((r[iAddr] ?? '').trim().toLowerCase());
    if (a === undefined) {
      plan.unknown++;
      continue;
    }
    if (seen.has(a.anonymous_id)) continue; // a repeated address: the first row wins
    seen.add(a.anonymous_id);
    // A blank cell or a missing column keeps what is there; Apple needs a label on every alias.
    const label = iLabel >= 0 ? unformula(r[iLabel] ?? '').trim().slice(0, LABEL_MAX) || (a.label ?? '') : (a.label ?? '');
    const note = iNote >= 0 && (r[iNote] ?? '') !== '' ? unformula(r[iNote]!).slice(0, NOTE_MAX) : (a.note ?? '');
    if (label === (a.label ?? '') && note === (a.note ?? '')) plan.unchanged++;
    else if (!a.is_active) plan.inactive++;
    else if (label === '') plan.unchanged++;
    else plan.updates.push({ alias: a, label, note });
  }
  return plan;
}

export interface Stats {
  total: number;
  active: number;
  inactive: number;
  lastMonth: number;
  domains: [string, number][];
}

/** Counts for the stats dialog; `domains` is the five most used sites. */
export function statsOf(list: Alias[], now = Date.now()): Stats {
  const byDomain = new Map<string, number>();
  let active = 0;
  let lastMonth = 0;
  for (const a of list) {
    if (a.is_active) active++;
    if (typeof a.created_ms === 'number' && now - a.created_ms < 30 * 86_400_000) lastMonth++;
    if (a.domain) byDomain.set(a.domain, (byDomain.get(a.domain) ?? 0) + 1);
  }
  const domains = [...byDomain].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])).slice(0, 5);
  return { total: list.length, active, inactive: list.length - active, lastMonth, domains };
}

/** Starts a browser download of `text`. */
export function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
