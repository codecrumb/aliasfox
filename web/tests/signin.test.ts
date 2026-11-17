// Multi-mode signed-out views: SignIn, SignUp and the email-code step. Neon Auth and the session
// store are fakes; /api/config is set directly on the real config store.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';

vi.mock('../src/lib/neonAuth', async (orig) => {
  const real = await orig<typeof import('../src/lib/neonAuth')>();
  return {
    ...real,
    neonAuth: {
      signUp: vi.fn(),
      signIn: vi.fn(),
      getToken: vi.fn(),
      verifyEmailCode: vi.fn(),
      sendVerificationCode: vi.fn(),
      signOut: vi.fn(async () => {}),
      deleteNeonUser: vi.fn()
    }
  };
});
vi.mock('../src/lib/session', async () => {
  const { writable } = await import('svelte/store');
  const store = writable({ state: 'login', mode: null, accountDeleted: null as string | null });
  return {
    __store: store,
    sessionStore: { subscribe: store.subscribe, exchange: vi.fn(async () => {}), boot: vi.fn() }
  };
});

import type { Writable } from 'svelte/store';
import * as sessionMod from '../src/lib/session';
import { ApiError } from '../src/lib/api';
import { appConfig } from '../src/lib/config';
import { NeonAuthError, neonAuth } from '../src/lib/neonAuth';
import SignIn from '../src/components/SignIn.svelte';
import SignUp from '../src/components/SignUp.svelte';

const n = vi.mocked(neonAuth);
const exchange = vi.mocked(sessionMod.sessionStore.exchange);
const sessionState = (sessionMod as unknown as { __store: Writable<Record<string, unknown>> }).__store;
let order: string[];
let replace: ReturnType<typeof vi.spyOn>;

function config(p: { signupsOpen?: boolean; contact?: string | null } = {}) {
  appConfig.set({
    loaded: true,
    mode: 'multi',
    signupsOpen: p.signupsOpen ?? true,
    neonAuthUrl: 'https://auth.example/neondb/auth',
    contact: p.contact ?? null
  });
}

const email = () => screen.getByLabelText('Email') as HTMLInputElement;
const password = () => screen.getByLabelText('Password') as HTMLInputElement;
const type = (el: HTMLInputElement, value: string) => fireEvent.input(el, { target: { value } });
const alertText = () => screen.getAllByRole('alert').map((a) => a.textContent ?? '').join(' ');

async function fillAndSubmit(e = 'me@example.com', p = 'correct horse 1!') {
  await type(email(), e);
  await type(password(), p);
  await fireEvent.submit(email().closest('form') as HTMLFormElement);
}

beforeEach(() => {
  order = [];
  for (const fn of Object.values(n)) fn.mockReset();
  n.signIn.mockImplementation(async () => void order.push('signIn'));
  n.getToken.mockImplementation(async () => {
    order.push('getToken');
    return 'jwt-1';
  });
  n.signUp.mockImplementation(async () => {
    order.push('signUp');
    return { needsVerification: false };
  });
  n.verifyEmailCode.mockImplementation(async () => void order.push('verify'));
  n.sendVerificationCode.mockImplementation(async () => void order.push('sendCode'));
  n.signOut.mockImplementation(async () => void order.push('signOut'));
  exchange.mockReset().mockImplementation(async (t: string) => void order.push(`exchange:${t}`));
  sessionState.set({ state: 'login', mode: null, accountDeleted: null });
  replace = vi.spyOn(history, 'replaceState');
  config();
});
afterEach(() => {
  cleanup();
  replace.mockRestore();
});

describe('SignIn', () => {
  it('has email + current-password fields, focuses email, and an alert region', () => {
    render(SignIn, { onNavigate: vi.fn() });
    expect(email().type).toBe('email');
    expect(email().getAttribute('autocomplete')).toBe('email');
    expect(password().type).toBe('password');
    expect(password().getAttribute('autocomplete')).toBe('current-password');
    expect(email()).toHaveFocus();
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Log in' })).toBeInTheDocument();
  });

  it('success: signIn, then getToken, then exchange (which loads the app), then the URL becomes /', async () => {
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(order).toEqual(['signIn', 'getToken', 'exchange:jwt-1']));
    expect(n.signIn).toHaveBeenCalledWith('me@example.com', 'correct horse 1!');
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(replace.mock.calls.at(-1)?.[2]).toBe('/');
  });

  it('wrong password: mapped message, no token and no exchange', async () => {
    n.signIn.mockRejectedValue(new NeonAuthError('invalid_credentials'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toContain('Incorrect email or password.'));
    expect(n.getToken).not.toHaveBeenCalled();
    expect(exchange).not.toHaveBeenCalled();
    expect(password().value).toBe('');
    expect(email().value).toBe('me@example.com');
  });

  it('network failure shows fixed text', async () => {
    n.signIn.mockRejectedValue(new TypeError('SECRET'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toContain("Can't reach the sign-in service"));
    expect(document.body.textContent).not.toContain('SECRET');
  });

  it('exchange signups_closed: the closed message, nothing more, and Neon is signed out', async () => {
    exchange.mockRejectedValue(new ApiError(403, 'signups_closed'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() =>
      expect(alertText()).toContain("Sign-ups are closed for now, and this account isn't on the list.")
    );
    expect(n.signOut).toHaveBeenCalledTimes(1);
    expect(replace).not.toHaveBeenCalled();
  });

  it('exchange account_disabled: the suspended message, no server text', async () => {
    exchange.mockRejectedValue(new ApiError(403, 'account_disabled'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toContain('This account has been suspended.'));
    expect(alertText()).not.toContain('account_disabled');
    expect(replace).not.toHaveBeenCalled();
  });

  it('exchange 429: try again in a minute', async () => {
    exchange.mockRejectedValue(new ApiError(429, 'too_many_attempts', { retryAfter: 60 }));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toContain('Too many attempts, try again in a minute.'));
  });

  it('exchange email_not_verified: prompts for the emailed code, then signs in again', async () => {
    exchange.mockRejectedValueOnce(new ApiError(403, 'email_not_verified'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    const code = (await screen.findByLabelText('Code')) as HTMLInputElement;
    expect(screen.getByText(/me@example\.com/)).toBeInTheDocument();
    expect(n.sendVerificationCode).toHaveBeenCalledWith('me@example.com');
    order = [];
    await type(code, '123456');
    await fireEvent.submit(code.closest('form') as HTMLFormElement);
    await waitFor(() => expect(order).toEqual(['verify', 'signIn', 'getToken', 'exchange:jwt-1']));
    expect(n.verifyEmailCode).toHaveBeenCalledWith('me@example.com', '123456');
  });

  it('Neon unverified on sign-in also goes to the code step', async () => {
    n.signIn.mockRejectedValueOnce(new NeonAuthError('unverified'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    expect(await screen.findByLabelText('Code')).toBeInTheDocument();
  });

  it('offers sign-up only when sign-ups are open; the link navigates without a reload', async () => {
    const onNavigate = vi.fn();
    render(SignIn, { onNavigate });
    const link = screen.getByRole('link', { name: 'Create an account' });
    expect(link.getAttribute('href')).toBe('/signup');
    await fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith('/signup');
    cleanup();
    config({ signupsOpen: false });
    render(SignIn, { onNavigate });
    expect(screen.getByRole('link', { name: 'Create an account' })).toBeInTheDocument();
    expect(screen.getByText(/Invited\?/)).toBeInTheDocument();
  });

  it('after account deletion: says so, and the pending line names the contact', () => {
    config({ contact: 'privacy@example.com' });
    sessionState.set({ state: 'login', mode: null, accountDeleted: 'pending' });
    render(SignIn, { onNavigate: vi.fn() });
    expect(screen.getByText('Your account is deleted.')).toBeInTheDocument();
    expect(
      screen.getByText('Your sign-in record is removed on request: write to privacy@example.com')
    ).toBeInTheDocument();
  });

  it('after a complete deletion there is no pending line', () => {
    sessionState.set({ state: 'login', mode: null, accountDeleted: 'deleted' });
    render(SignIn, { onNavigate: vi.fn() });
    expect(screen.getByText('Your account is deleted.')).toBeInTheDocument();
    expect(screen.queryByText(/removed on request/)).toBeNull();
  });
});

describe('SignUp', () => {
  it('closed: an invite-only note, the form stays, and a link to log in', async () => {
    config({ signupsOpen: false });
    const onNavigate = vi.fn();
    render(SignUp, { onNavigate });
    expect(screen.getByText(/invite-only for now/)).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Log in' });
    expect(link.getAttribute('href')).toBe('/login');
    await fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith('/login');
    expect(n.signUp).not.toHaveBeenCalled();
  });

  it('open: email + new-password fields with the hint text (a hint, not a rule)', () => {
    render(SignUp, { onNavigate: vi.fn() });
    expect(email().getAttribute('autocomplete')).toBe('email');
    expect(password().getAttribute('autocomplete')).toBe('new-password');
    const hint = screen.getByText('At least 12 characters, with a digit and a symbol.');
    expect(password().getAttribute('aria-describedby')).toContain(hint.id);
    expect(email()).toHaveFocus();
  });

  it('success without verification: signUp, getToken, exchange', async () => {
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(order).toEqual(['signUp', 'getToken', 'exchange:jwt-1']));
    expect(n.signUp).toHaveBeenCalledWith('me@example.com', 'correct horse 1!');
  });

  it('signed up with a session but email unverified: the exchange refusal opens the code step and asks for a code', async () => {
    exchange.mockReset().mockRejectedValueOnce(new ApiError(403, 'email_not_verified'));
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    const code = (await screen.findByLabelText('Code')) as HTMLInputElement;
    expect(n.sendVerificationCode).toHaveBeenCalledWith('me@example.com');
    exchange.mockImplementation(async (t: string) => void order.push(`exchange:${t}`));
    await type(code, '654321');
    await fireEvent.submit(code.closest('form') as HTMLFormElement);
    await waitFor(() => expect(n.verifyEmailCode).toHaveBeenCalledWith('me@example.com', '654321'));
  });

  it('success with verification: code step, then verify, sign in, token, exchange', async () => {
    n.signUp.mockResolvedValueOnce({ needsVerification: true });
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    const code = (await screen.findByLabelText('Code')) as HTMLInputElement;
    expect(code.getAttribute('autocomplete')).toBe('one-time-code');
    expect(n.getToken).not.toHaveBeenCalled();
    await type(code, '654321');
    await fireEvent.submit(code.closest('form') as HTMLFormElement);
    await waitFor(() => expect(order).toEqual(['verify', 'signIn', 'getToken', 'exchange:jwt-1']));
    expect(n.verifyEmailCode).toHaveBeenCalledWith('me@example.com', '654321');
  });

  it('wrong code: mapped message, stays on the code step; a new code can be sent', async () => {
    n.signUp.mockResolvedValueOnce({ needsVerification: true });
    n.verifyEmailCode.mockRejectedValueOnce(new NeonAuthError('bad_code'));
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    const code = (await screen.findByLabelText('Code')) as HTMLInputElement;
    await type(code, '000000');
    await fireEvent.submit(code.closest('form') as HTMLFormElement);
    await waitFor(() => expect(alertText()).toContain("That code didn't work"));
    expect(exchange).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Send a new code' }));
    await waitFor(() => expect(n.sendVerificationCode).toHaveBeenCalledWith('me@example.com'));
    expect(await screen.findByText('A new code is on its way.')).toBeInTheDocument();
  });

  it.each([
    ['email_taken', /already exists/],
    ['weak_password', /stronger password/]
  ] as const)('%s shows its fixed message', async (kind, text) => {
    n.signUp.mockRejectedValueOnce(new NeonAuthError(kind));
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toMatch(text));
    expect(exchange).not.toHaveBeenCalled();
  });

  it('exchange signups_closed after a fresh sign-up shows the closed message', async () => {
    exchange.mockRejectedValueOnce(new ApiError(403, 'signups_closed'));
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() =>
      expect(alertText()).toContain("Sign-ups are closed for now, and this account isn't on the list.")
    );
  });

  it('has a link back to log in', async () => {
    const onNavigate = vi.fn();
    render(SignUp, { onNavigate });
    await fireEvent.click(screen.getByRole('link', { name: 'Log in' }));
    expect(onNavigate).toHaveBeenCalledWith('/login');
  });
});

describe('accessibility (axe; same disabled jsdom-only rules as tests/a11y.test.ts)', () => {
  const AXE = {
    rules: {
      'color-contrast': { enabled: false },
      region: { enabled: false },
      'scrollable-region-focusable': { enabled: false }
    }
  };
  async function clean() {
    const { axe } = await import('vitest-axe');
    const r = await axe(document.body, AXE);
    expect(r.violations.map((v) => v.id)).toEqual([]);
    expect(r.passes.length).toBeGreaterThan(0);
  }

  it('SignIn, with an error and the deleted notice', async () => {
    sessionState.set({ state: 'login', mode: null, accountDeleted: 'pending' });
    n.signIn.mockRejectedValue(new NeonAuthError('invalid_credentials'));
    render(SignIn, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await waitFor(() => expect(alertText()).toContain('Incorrect'));
    await clean();
  });

  it('SignUp form, closed message and code step', async () => {
    render(SignUp, { onNavigate: vi.fn() });
    await clean();
    cleanup();
    config({ signupsOpen: false });
    render(SignUp, { onNavigate: vi.fn() });
    await clean();
    cleanup();
    config();
    n.signUp.mockResolvedValueOnce({ needsVerification: true });
    render(SignUp, { onNavigate: vi.fn() });
    await fillAndSubmit();
    await screen.findByLabelText('Code');
    await clean();
  });
});
