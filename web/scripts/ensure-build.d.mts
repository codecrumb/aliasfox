export const SOURCES: string[];
export const EXIT: { FRESH: number; FAILED: number; STALE: number; MISSING: number; NO_NPM: number };
export const DEFAULT_WEB: string;
export const DEFAULT_INDEX: string;
export function newestMtime(paths: string[]): number;
export function bundleState(web?: string, index?: string): 'missing' | 'stale' | 'fresh';
export function npmAvailable(): boolean;
export class NoNpmError extends Error {
  state: 'missing' | 'stale';
}
export function ensureBuild(opts?: {
  web?: string;
  index?: string;
  run?: (web: string, args: string[]) => void;
  hasNpm?: () => boolean;
}): { state: 'missing' | 'stale' | 'fresh'; built: boolean };
