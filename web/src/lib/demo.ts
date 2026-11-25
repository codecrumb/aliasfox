import { ApiError, api } from './api';
import type { Alias } from './types';

// The /demo page: every API call is answered from memory, so nothing reaches the server or
// Apple and nothing is stored (a reload resets it). Addresses use the reserved `.invalid` TLD.

const DAY = 86_400_000;
const FORWARD = 'you@example.com';

function seed(): Alias[] {
  const now = Date.now();
  const mk = (
    i: number,
    word: string,
    label: string,
    ageDays: number,
    extra: Partial<Alias> = {}
  ): Alias => ({
    anonymous_id: `demo-${i}`,
    hme: `${word}@demo.invalid`,
    label,
    note: null,
    is_active: true,
    forward_to: FORWARD,
    origin: 'ON_DEMAND',
    domain: null,
    created_ms: now - ageDays * DAY,
    origin_app_name: null,
    app_bundle_id: null,
    ...extra
  });
  return [
    mk(1, 'maple.quiet.4821', 'Netflix', 3, { domain: 'netflix.com' }),
    mk(2, 'river_lamp_093', 'Newsletter: The Daily Brief', 9, { note: 'Unsubscribe if it gets noisy' }),
    mk(3, 'copper.fern.77', 'Online shop', 21, { domain: 'shop.example' }),
    mk(4, 'lunar-owl-5502', 'Airline loyalty', 40, { domain: 'airline.example' }),
    mk(5, 'pine.echo.318', 'Spam magnet', 55, { is_active: false, note: 'Deactivated: sold my address' }),
    mk(6, 'ember_wave_620', 'Fitness app', 70, { origin: 'SAFARI', origin_app_name: 'Fitness' }),
    mk(7, 'violet-sky-1184', 'Bank statements', 95),
    mk(8, 'cedar.moss.906', 'Conference signup 2025', 130, { is_active: false }),
    mk(9, 'quartz_bird_41', 'GitHub', 180, { domain: 'github.com' }),
    mk(10, 'amber.dune.2270', 'Food delivery', 240, { domain: 'delivery.example' }),
    mk(11, 'frost-kite-733', 'Forum account', 300),
    mk(12, 'sage.tide.588', 'Parking app', 365)
  ];
}

const WORDS = ['maple', 'river', 'copper', 'lunar', 'pine', 'ember', 'violet', 'cedar', 'quartz', 'amber', 'frost', 'sage'];
const word = (): string => WORDS[Math.floor(Math.random() * WORDS.length)]!;

function leave(): never {
  location.assign('/');
  throw new ApiError(0, 'network_error');
}

export function installDemo(): void {
  let list = seed();
  let selected = FORWARD;
  let counter = 100;
  const ok = async (): Promise<{ ok: boolean }> => ({ ok: true });
  const find = (id: string): Alias => {
    const a = list.find((x) => x.anonymous_id === id);
    if (!a) throw new ApiError(404, 'not_found');
    return a;
  };
  const replace = (id: string, f: (a: Alias) => Alias): void => {
    find(id);
    list = list.map((a) => (a.anonymous_id === id ? f(a) : a));
  };

  const mock = {
    config: async () => ({ mode: 'single' }),
    // A non-key string keeps the aliases store in memory-only mode: no IndexedDB cache.
    session: async () => ({ cache_key: 'demo', mode: 'single', auth_enabled: true }),
    status: async () => ({ connected: true, mode: 'single', auth_enabled: true, hme_available: true }),
    statusRecheck: async () => ({ connected: true, hme_available: true }),
    listAliases: async () => list.map((a) => ({ ...a })),
    generate: async () => ({ hme: `${word()}.${word()}.${Math.floor(1000 + Math.random() * 9000)}@demo.invalid` }),
    reserve: async (b: { hme: string; label: string; note: string }) => {
      const created: Alias = {
        anonymous_id: `demo-${++counter}`,
        hme: b.hme,
        label: b.label || null,
        note: b.note || null,
        is_active: true,
        forward_to: selected,
        origin: 'ON_DEMAND',
        domain: null,
        created_ms: Date.now(),
        origin_app_name: null,
        app_bundle_id: null
      };
      list = [created, ...list];
      return { ...created };
    },
    patch: async (id: string, b: { label: string; note?: string }) => {
      replace(id, (a) => ({ ...a, label: b.label || null, note: b.note ? b.note : null }));
      return ok();
    },
    setActive: async (id: string, active: boolean) => {
      replace(id, (a) => ({ ...a, is_active: active }));
      return ok();
    },
    remove: async (id: string) => {
      find(id);
      list = list.filter((a) => a.anonymous_id !== id);
      return ok();
    },
    forwardOptions: async () => ({ options: [FORWARD, 'work@example.com'], selected }),
    setForwardTo: async (address: string) => {
      selected = address;
      return ok();
    },
    logout: async () => leave(),
    disconnect: async () => leave(),
    deleteAccount: async () => leave()
  };
  for (const k of Object.keys(mock) as (keyof typeof mock)[]) {
    (api as Record<string, unknown>)[k] = mock[k];
  }
}
