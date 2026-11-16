import { ApiError } from './api';

// User-facing text for failed alias actions. Server messages are untrusted and are only ever
// rendered as text; ApiError already caps their length.

const LIMITED = new Set(['rate_limited', 'too_many_attempts', 'busy']);
const UNREACHABLE = new Set(['apple_unreachable', 'network_error', 'bad_response']);
const SESSION = new Set(['session_expired', 'session_unreadable', 'not_connected']);

export const SESSION_EXPIRED_TEXT = 'Your Apple session expired. Reconnect, then try again.';

function seconds(n: number): string {
  return `${n} ${n === 1 ? 'second' : 'seconds'}`;
}

function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'network_error';
}

export function isSessionError(e: unknown): boolean {
  return SESSION.has(detailOf(e));
}

export function isRateLimited(e: unknown): boolean {
  return LIMITED.has(detailOf(e));
}

export function retryText(e: unknown): string {
  const n = e instanceof ApiError ? e.retryAfter : undefined;
  return n !== undefined && n > 0 ? `Try again in ${seconds(Math.ceil(n))}.` : 'Try again in a moment.';
}

/** A short explanation of why an action failed. `fallback` is used for unknown errors. */
export function describeError(e: unknown, fallback = 'Something went wrong. Try again.'): string {
  const d = detailOf(e);
  if (LIMITED.has(d)) return `Too many requests. ${retryText(e)}`;
  if (UNREACHABLE.has(d)) return "Can't reach Apple right now.";
  if (d === 'apple_error') {
    const m = e instanceof ApiError ? (e.serverMessage ?? '') : '';
    return `Apple returned an error. ${m}`.trim();
  }
  if (SESSION.has(d)) return SESSION_EXPIRED_TEXT;
  if (d === 'not_authenticated') return 'You were logged out. Log in again.';
  return fallback;
}

/** The generate step has its own rate-limit wording. */
export function describeGenerateError(e: unknown): string {
  if (isRateLimited(e)) return `Too many new addresses. ${retryText(e)}`;
  return describeError(e, "Couldn't get a new address. Try again.");
}
