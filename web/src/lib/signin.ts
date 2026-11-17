import { ApiError } from './api';
import { NeonAuthError, neonAuth, neonAuthMessage } from './neonAuth';
import { sessionStore } from './session';

// Multi mode sign-in: Neon Auth session -> JWT -> POST /api/auth/exchange -> our cookie -> the app.
// Every message here is fixed text; server text is never shown.

export const SIGNUPS_CLOSED_TEXT = "Sign-ups are closed for now, and this account isn't on the list.";
export const ACCOUNT_DISABLED_TEXT = 'This account has been suspended.';
export const TOO_MANY_TEXT = 'Too many attempts, try again in a minute.';
const VERIFY_TEXT = 'Verify your email to continue.';
const REJECTED_TEXT = "Sign-in didn't go through. Try again.";
const UNREACHABLE_TEXT = "Can't reach AliasFox. Check your connection and try again.";

const LIMITED = new Set(['too_many_attempts', 'rate_limited', 'busy']);

/** With a Neon Auth session already in the browser: get a JWT and exchange it. */
export async function enterWithSession(): Promise<void> {
  const token = await neonAuth.getToken();
  try {
    await sessionStore.exchange(token);
  } catch (e) {
    // Refused for good: do not leave the browser signed in to Neon Auth.
    if (e instanceof ApiError && (e.detail === 'signups_closed' || e.detail === 'invalid_token')) {
      void neonAuth.signOut().catch(() => {});
    }
    throw e;
  }
  history.replaceState(history.state, '', '/');
}

export async function enterApp(email: string, password: string): Promise<void> {
  await neonAuth.signIn(email, password);
  await enterWithSession();
}

/** Neon or AliasFox says the email is not verified yet: offer the code step. */
export function needsVerification(e: unknown): boolean {
  return (
    (e instanceof NeonAuthError && e.kind === 'unverified') ||
    (e instanceof ApiError && e.detail === 'email_not_verified')
  );
}

export function signInErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.detail === 'signups_closed') return SIGNUPS_CLOSED_TEXT;
    if (e.detail === 'account_disabled') return ACCOUNT_DISABLED_TEXT;
    if (e.status === 429 || LIMITED.has(e.detail)) return TOO_MANY_TEXT;
    if (e.detail === 'email_not_verified') return VERIFY_TEXT;
    if (e.detail === 'invalid_token') return REJECTED_TEXT;
    return UNREACHABLE_TEXT;
  }
  return neonAuthMessage(e);
}
