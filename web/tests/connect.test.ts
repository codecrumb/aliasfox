import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { writable } from 'svelte/store';
import { ApiError } from '../src/lib/api';

vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return {
    ...real,
    api: { connectLogin: vi.fn(), connectVerify: vi.fn(), connectResend: vi.fn(), connectCookie: vi.fn() }
  };
});
vi.mock('../src/lib/session', async () => {
  const { writable } = await import('svelte/store');
  const store = writable({ state: 'connect', reason: null as string | null });
  return {
    __store: store,
    sessionStore: {
      subscribe: store.subscribe,
      logout: vi.fn(async () => {}),
      markReady: vi.fn(async () => {}),
      onApiError: vi.fn()
    }
  };
});
vi.mock('../src/lib/toasts', () => ({ toasts: { push: vi.fn() } }));

import { api } from '../src/lib/api';
import * as sessionMod from '../src/lib/session';
import { toasts } from '../src/lib/toasts';
import ConnectApple from '../src/components/ConnectApple.svelte';

const store = (sessionMod as unknown as { __store: ReturnType<typeof writable<{ state: string; reason: string | null }>> }).__store;
const sess = vi.mocked(sessionMod.sessionStore);
const login = vi.mocked(api.connectLogin);
const verify = vi.mocked(api.connectVerify);
const resend = vi.mocked(api.connectResend);
const cookieApi = vi.mocked(api.connectCookie);

beforeEach(() => {
  [login, verify, resend, cookieApi, sess.logout, sess.markReady, sess.onApiError, vi.mocked(toasts.push)].forEach((m) =>
    m.mockReset()
  );
  sess.markReady.mockResolvedValue(undefined);
  sess.logout.mockResolvedValue(undefined);
  store.set({ state: 'connect', reason: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const idField = () => screen.getByLabelText('Apple ID') as HTMLInputElement;
const pwField = () => screen.getByLabelText('Password') as HTMLInputElement;
const codeBoxes = () => screen.getAllByRole('textbox', { name: /Digit \d of 6/ }) as HTMLInputElement[];

async function toPassword(id = 'me@icloud.com') {
  await fireEvent.input(idField(), { target: { value: id } });
  await fireEvent.submit(idField().closest('form') as HTMLFormElement);
  await screen.findByLabelText('Password');
}
async function submitPassword(pw = 'hunter2') {
  await fireEvent.input(pwField(), { target: { value: pw } });
  await fireEvent.submit(pwField().closest('form') as HTMLFormElement);
}
async function typeCode(code: string) {
  for (let i = 0; i < 6; i++) {
    await fireEvent.input(codeBoxes()[i] as HTMLElement, { target: { value: code[i] } });
  }
}
async function toCode() {
  login.mockResolvedValue({ status: 'needs_2fa', flow_id: 'flow-1' });
  await toPassword();
  await submitPassword();
  await screen.findByRole('group', { name: 'Verification code' });
}
const openCookie = async () => {
  await fireEvent.click(screen.getByRole('button', { name: 'Use a cookie instead' }));
  return screen.getByLabelText(/cookie/i, { selector: 'textarea' }) as HTMLTextAreaElement;
};

describe('ConnectApple steps', () => {
  it('test_steps_advance_email_then_password_then_code', async () => {
    login.mockResolvedValue({ status: 'needs_2fa', flow_id: 'flow-1' });
    render(ConnectApple);
    expect(idField().type).toBe('email');
    expect(idField().getAttribute('autocomplete')).toBe('username');
    expect(idField().getAttribute('inputmode')).toBe('email');
    expect(idField().required).toBe(true);
    expect(idField()).toHaveFocus();
    expect(screen.queryByLabelText('Password')).toBeNull();
    await toPassword();
    expect(pwField().type).toBe('password');
    expect(pwField().getAttribute('autocomplete')).toBe('current-password');
    const hidden = document.querySelector('input[autocomplete="username"][readonly]') as HTMLInputElement;
    expect(hidden.value).toBe('me@icloud.com');
    expect(pwField()).toHaveFocus();
    await submitPassword();
    expect(login).toHaveBeenCalledExactlyOnceWith({ apple_id: 'me@icloud.com', password: 'hunter2' });
    await screen.findByRole('group', { name: 'Verification code' });
    await typeCode('123456');
    await waitFor(() => expect(verify).toHaveBeenCalledExactlyOnceWith({ flow_id: 'flow-1', code: '123456' }));
  });

  it('Enter on empty Apple ID does not advance', async () => {
    render(ConnectApple);
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    expect(screen.queryByLabelText('Password')).toBeNull();
  });

  it('Back returns to the Apple ID step with it kept', async () => {
    render(ConnectApple);
    await toPassword();
    await fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect((await screen.findByLabelText('Apple ID') as HTMLInputElement).value).toBe('me@icloud.com');
  });

  it('login without 2FA toasts, marks ready', async () => {
    login.mockResolvedValue({ status: 'ok' });
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    await waitFor(() => expect(sess.markReady).toHaveBeenCalledTimes(1));
    expect(toasts.push).toHaveBeenCalledWith({ kind: 'ok', text: 'Apple account connected' });
  });

  it('verify success toasts and marks ready', async () => {
    verify.mockResolvedValue({ status: 'ok' });
    render(ConnectApple);
    await toCode();
    await typeCode('654321');
    await waitFor(() => expect(sess.markReady).toHaveBeenCalledTimes(1));
    expect(toasts.push).toHaveBeenCalledWith({ kind: 'ok', text: 'Apple account connected' });
  });

  it('China checkbox sends china:true', async () => {
    login.mockResolvedValue({ status: 'ok' });
    render(ConnectApple);
    await toPassword();
    await fireEvent.click(screen.getByLabelText('My Apple ID is for mainland China'));
    await submitPassword();
    expect(login).toHaveBeenCalledWith({ apple_id: 'me@icloud.com', password: 'hunter2', china: true });
  });

  it('test_password_cleared_from_state_after_submit', async () => {
    let reject: (e: unknown) => void = () => {};
    login.mockReturnValue(new Promise((_, r) => (reject = r)));
    render(ConnectApple);
    await toPassword();
    await submitPassword('s3cret-pw');
    reject(new ApiError(401, 'apple_login_failed'));
    await screen.findByText(/Apple didn't accept those details/);
    expect(pwField().value).toBe('');
    expect(document.body.innerHTML).not.toContain('s3cret-pw');
    const hidden = document.querySelector('input[readonly]') as HTMLInputElement;
    expect(hidden.value).toBe('me@icloud.com');
  });

  it('apple_login_failed stays on password step, Apple ID kept, password focused', async () => {
    login.mockRejectedValue(new ApiError(401, 'apple_login_failed'));
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Apple didn't accept those details. Check your Apple ID and password."
    );
    await waitFor(() => expect(pwField()).toHaveFocus());
    expect((document.querySelector('input[readonly]') as HTMLInputElement).value).toBe('me@icloud.com');
  });

  it('test_flow_expired_returns_to_password_step_keeping_apple_id', async () => {
    verify.mockRejectedValue(new ApiError(410, 'flow_expired'));
    render(ConnectApple);
    await toCode();
    await typeCode('111111');
    expect(await screen.findByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('That sign-in took too long. Enter your password again.');
    expect((document.querySelector('input[readonly]') as HTMLInputElement).value).toBe('me@icloud.com');
    expect(pwField().value).toBe('');
  });

  it('invalid_code: code error state, boxes cleared, first focused, retry allowed', async () => {
    verify.mockRejectedValueOnce(new ApiError(401, 'invalid_code')).mockResolvedValueOnce({ status: 'ok' });
    render(ConnectApple);
    await toCode();
    await typeCode('111111');
    const group = screen.getByRole('group', { name: 'Verification code' });
    await waitFor(() => expect(group).toHaveClass('invalid'));
    expect(codeBoxes().every((b) => b.value === '')).toBe(true);
    await waitFor(() => expect(codeBoxes()[0]).toHaveFocus());
    await typeCode('222222');
    await waitFor(() => expect(sess.markReady).toHaveBeenCalled());
    expect(verify).toHaveBeenCalledTimes(2);
  });

  it('test_rate_limited_message_shows_retry_after', async () => {
    vi.useFakeTimers();
    login.mockRejectedValue(new ApiError(429, 'rate_limited', { retryAfter: 3 }));
    const { container } = render(ConnectApple);
    await fireEvent.input(idField(), { target: { value: 'a@b.c' } });
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    await vi.advanceTimersByTimeAsync(0);
    await fireEvent.input(pwField(), { target: { value: 'x' } });
    await fireEvent.submit(pwField().closest('form') as HTMLFormElement);
    await vi.advanceTimersByTimeAsync(0);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Too many attempts. Try again in about 3 seconds.');
    expect(container.querySelector('[aria-hidden="true"].countdown')).toHaveTextContent('in 3 seconds');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(container.querySelector('.countdown')).toHaveTextContent('in 2 seconds');
    expect(alert).toHaveTextContent('about 3 seconds');
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    expect(container.querySelector('[role="status"][aria-live="polite"]')).toHaveTextContent(
      'You can try again now.'
    );
    expect(container.querySelector('.countdown')).toBeNull();
  });

  it.each(['apple_unreachable', 'network_error', 'bad_response'])('%s shows the retryable message', async (d) => {
    login.mockRejectedValue(new ApiError(502, d));
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Can't reach Apple right now. Try again in a moment."
    );
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
  });

  it('apple_error shows fixed text plus serverMessage as inert text', async () => {
    login.mockRejectedValue(new ApiError(502, 'apple_error', { serverMessage: '<img src=x onerror=1>' }));
    const { container } = render(ConnectApple);
    await toPassword();
    await submitPassword();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Apple returned an error.');
    expect(alert.textContent).toContain('<img src=x onerror=1>');
    expect(container.querySelector('img')).toBeNull();
  });

  it('unknown errors show generic text, never raw server text', async () => {
    login.mockRejectedValue(new ApiError(500, 'internal_error', { serverMessage: 'SECRET' }));
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Try again.');
    expect(document.body.textContent).not.toContain('SECRET');
  });

  it('not_authenticated goes to the store', async () => {
    const err = new ApiError(401, 'not_authenticated');
    login.mockRejectedValue(err);
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    await waitFor(() => expect(sess.onApiError).toHaveBeenCalledWith(err));
  });

  it('Log out calls the store', async () => {
    render(ConnectApple);
    await fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(sess.logout).toHaveBeenCalledTimes(1);
  });
});

describe('resend code', () => {
  const resendButton = () => screen.getByRole('button', { name: 'Resend code' }) as HTMLButtonElement;

  it('is only on the code step, and asks the server for another code for this flow', async () => {
    resend.mockResolvedValue({ status: 'ok' });
    render(ConnectApple);
    await toPassword();
    expect(screen.queryByRole('button', { name: 'Resend code' })).toBeNull();
    login.mockResolvedValue({ status: 'needs_2fa', flow_id: 'flow-1' });
    await submitPassword();
    await screen.findByRole('group', { name: 'Verification code' });
    await fireEvent.click(resendButton());
    await waitFor(() => expect(resend).toHaveBeenCalledWith({ flow_id: 'flow-1' }));
    expect(await screen.findByText('A new code is on its way.')).toBeInTheDocument();
    expect(login).toHaveBeenCalledTimes(1); // no new sign-in
  });

  it('is disabled while the request is in flight', async () => {
    let done: (v: { status: 'ok' }) => void = () => {};
    resend.mockReturnValue(new Promise((r) => (done = r)));
    render(ConnectApple);
    await toCode();
    await fireEvent.click(resendButton());
    await waitFor(() => expect(resendButton()).toBeDisabled());
    await fireEvent.click(resendButton());
    expect(resend).toHaveBeenCalledTimes(1);
    done({ status: 'ok' });
    await waitFor(() => expect(resendButton()).not.toBeDisabled());
  });

  it('flow_expired goes back to the password step with the expiry message', async () => {
    resend.mockRejectedValue(new ApiError(410, 'flow_expired'));
    render(ConnectApple);
    await toCode();
    await fireEvent.click(resendButton());
    await screen.findByLabelText('Password');
    expect(screen.getByRole('alert')).toHaveTextContent('That sign-in took too long. Enter your password again.');
  });

  it('resend_limit says so and stops offering resends, without a lockout countdown', async () => {
    resend.mockRejectedValue(new ApiError(429, 'resend_limit'));
    render(ConnectApple);
    await toCode();
    await fireEvent.click(resendButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(/too many new codes/i);
    expect(resendButton()).toBeDisabled();
    expect(screen.queryByText(/Try again in/)).toBeNull();
    expect(codeBoxes()[0]).not.toBeDisabled();
  });

  it('a wrong code does not hide or disable Resend', async () => {
    verify.mockRejectedValueOnce(new ApiError(401, 'invalid_code'));
    render(ConnectApple);
    await toCode();
    await typeCode('111111');
    await waitFor(() => expect(screen.getByRole('group', { name: 'Verification code' })).toHaveClass('invalid'));
    expect(resendButton()).not.toBeDisabled();
  });

  it('the code step no longer claims the code was already sent', async () => {
    render(ConnectApple);
    await toCode();
    expect(screen.queryByText(/Apple sent a 6-digit code/)).toBeNull();
    expect(screen.getByText(/Approve the sign-in on your Apple devices/)).toBeInTheDocument();
  });
});

describe('banners', () => {
  it('no banner on plain connect', () => {
    render(ConnectApple);
    expect(screen.queryByText(/Reconnect to continue/)).toBeNull();
  });
  it.each([
    ['session_expired', 'Your Apple session expired. Reconnect to continue.'],
    [
      'session_unreadable',
      "AliasFox can't read the stored Apple session (the server key may have changed). Reconnect to continue."
    ]
  ])('reconnect with %s', (reason, text) => {
    store.set({ state: 'reconnect', reason });
    render(ConnectApple);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
});

describe('cookie text and the code step', () => {
  it('unsent cookie text is cleared when entering the code step', async () => {
    render(ConnectApple);
    const ta = await openCookie();
    await fireEvent.input(ta, { target: { value: 'secret=cookie-value' } });
    expect(ta.value).toBe('secret=cookie-value');
    await toCode();
    await fireEvent.click(screen.getByRole('button', { name: 'Back' })); // password step
    await fireEvent.click(screen.getByRole('button', { name: 'Back' })); // apple id step
    const again = screen.getByLabelText(/cookie/i, { selector: 'textarea' }) as HTMLTextAreaElement;
    expect(again.value).toBe('');
  });
});

describe('cookie panel', () => {
  it('is collapsed with aria-expanded and shows the warning when opened', async () => {
    render(ConnectApple);
    const btn = screen.getByRole('button', { name: 'Use a cookie instead' });
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText(/full iCloud web session/)).toBeNull();
    const ta = await openCookie();
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/A pasted cookie is your full iCloud web session/)).toBeInTheDocument();
    expect(screen.getByText(/Copy value/)).toBeInTheDocument();
    expect(ta.getAttribute('spellcheck')).toBe('false');
    expect(ta.getAttribute('autocomplete')).toBe('off');
    expect(ta.getAttribute('autocapitalize')).toBe('off');
    expect(ta.getAttribute('name')).toBeNull();
    expect(screen.getByRole('button', { name: 'Connect with cookie' })).toBeDisabled();
  });

  it('test_cookie_panel_truncated_hint_on_ellipsis', async () => {
    render(ConnectApple);
    const ta = await openCookie();
    const hint = 'This looks truncated — copy the cookie header again using Copy value.';
    await fireEvent.input(ta, { target: { value: 'X-APPLE-WEBAUTH=abc' } });
    expect(screen.queryByText(hint)).toBeNull();
    await fireEvent.input(ta, { target: { value: 'X-APPLE-WEBAUTH=abc…' } });
    expect(screen.getByText(hint)).toBeInTheDocument();
    await fireEvent.input(ta, { target: { value: 'a=b...' } });
    expect(screen.getByText(hint)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect with cookie' })).toBeEnabled();
  });

  it('test_cookie_connect_success_goes_ready', async () => {
    cookieApi.mockResolvedValue({ status: 'ok' });
    render(ConnectApple);
    const ta = await openCookie();
    await fireEvent.input(ta, { target: { value: ' a=b; c=d ' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Connect with cookie' }));
    await waitFor(() => expect(sess.markReady).toHaveBeenCalledTimes(1));
    expect(cookieApi).toHaveBeenCalledExactlyOnceWith('a=b; c=d');
    expect(toasts.push).toHaveBeenCalledWith({ kind: 'ok', text: 'Apple account connected' });
    expect(ta.value).toBe('');
  });

  it('cookie text is cleared after failure and serverMessage is inert text', async () => {
    cookieApi.mockRejectedValue(new ApiError(400, 'invalid_cookie', { serverMessage: '<img src=x onerror=1>' }));
    const { container } = render(ConnectApple);
    const ta = await openCookie();
    await fireEvent.input(ta, { target: { value: 'a=b' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Connect with cookie' }));
    await waitFor(() => expect(ta.value).toBe(''));
    expect(screen.getByText(/<img src=x onerror=1>/)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('invalid_cookie without a message uses the fixed line; 413 and unreachable have their own', async () => {
    render(ConnectApple);
    const ta = await openCookie();
    const run = async (err: ApiError) => {
      cookieApi.mockRejectedValueOnce(err);
      await fireEvent.input(ta, { target: { value: 'a=b' } });
      await fireEvent.click(screen.getByRole('button', { name: 'Connect with cookie' }));
    };
    await run(new ApiError(400, 'invalid_cookie'));
    expect(await screen.findByText("That cookie didn't work. Copy it again from your browser.")).toBeInTheDocument();
    await run(new ApiError(413, 'payload_too_large'));
    expect(await screen.findByText("That's too long to be a cookie header.")).toBeInTheDocument();
    await run(new ApiError(502, 'apple_unreachable'));
    expect(await screen.findByText("Can't reach Apple right now. Try again in a moment.")).toBeInTheDocument();
  });
});

describe('fix round 1', () => {
  const group = () => screen.getByRole('group', { name: 'Verification code' });
  const expectClean = () => {
    expect(group()).not.toHaveClass('invalid');
    expect(group()).not.toHaveClass('shake');
    expect(group().getAttribute('aria-invalid')).toBe('false');
    expect(document.querySelector('.status')?.textContent).toBe('');
  };

  it('wrong code -> Back -> password -> code screen is clean, and a second wrong code still shakes', async () => {
    verify.mockRejectedValue(new ApiError(401, 'invalid_code'));
    render(ConnectApple);
    await toCode();
    await typeCode('111111');
    await waitFor(() => expect(group()).toHaveClass('invalid'));
    await fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await screen.findByLabelText('Password');
    await submitPassword();
    await screen.findByRole('group', { name: 'Verification code' });
    expectClean();
    await typeCode('222222');
    await waitFor(() => expect(group()).toHaveClass('invalid'));
    expect(group()).toHaveClass('shake');
  });

  it('wrong code -> flow_expired -> password -> new code screen is clean', async () => {
    verify
      .mockRejectedValueOnce(new ApiError(401, 'invalid_code'))
      .mockRejectedValueOnce(new ApiError(410, 'flow_expired'));
    render(ConnectApple);
    await toCode();
    await typeCode('111111');
    await waitFor(() => expect(group()).toHaveClass('invalid'));
    await typeCode('222222');
    await screen.findByText('That sign-in took too long. Enter your password again.');
    await submitPassword();
    await screen.findByRole('group', { name: 'Verification code' });
    expectClean();
  });

  async function limitedAtPassword(retryAfter = 3) {
    login.mockRejectedValue(new ApiError(429, 'rate_limited', { retryAfter }));
    const r = render(ConnectApple);
    await fireEvent.input(idField(), { target: { value: 'a@b.c' } });
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    await vi.advanceTimersByTimeAsync(0);
    await fireEvent.input(pwField(), { target: { value: 'x' } });
    await fireEvent.submit(pwField().closest('form') as HTMLFormElement);
    await vi.advanceTimersByTimeAsync(0);
    return r;
  }

  it('rate limit is not bypassed by Back and Continue; polite end message appears once', async () => {
    vi.useFakeTimers();
    const { container } = await limitedAtPassword();
    await fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
    expect(container.querySelector('.countdown')).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1000);
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    expect(screen.queryByLabelText('Password')).toBeNull();
    await vi.advanceTimersByTimeAsync(2000);
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
    const said = Array.from(container.querySelectorAll('[role="status"]')).filter(
      (n) => n.textContent === 'You can try again now.'
    );
    expect(said).toHaveLength(1);
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    await screen.findByLabelText('Password');
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled();
  });

  it('cookie panel close/reopen stays locked; end announcement only in the cookie scope', async () => {
    vi.useFakeTimers();
    cookieApi.mockRejectedValue(new ApiError(429, 'too_many_attempts', { retryAfter: 3 }));
    const { container } = render(ConnectApple);
    const toggle = screen.getByRole('button', { name: 'Use a cookie instead' });
    await fireEvent.click(toggle);
    const ta = screen.getByLabelText(/cookie/i, { selector: 'textarea' });
    await fireEvent.input(ta, { target: { value: 'a=b' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Connect with cookie' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector('.countdown')).not.toBeNull();
    await fireEvent.click(toggle);
    await fireEvent.click(toggle);
    const ta2 = screen.getByLabelText(/cookie/i, { selector: 'textarea' });
    await fireEvent.input(ta2, { target: { value: 'c=d' } });
    expect(screen.getByRole('button', { name: 'Connect with cookie' })).toBeDisabled();
    expect(container.querySelector('.countdown')).not.toBeNull();
    await vi.advanceTimersByTimeAsync(3000);
    const said = Array.from(container.querySelectorAll('[role="status"]')).filter(
      (n) => n.textContent === 'You can try again now.'
    );
    expect(said).toHaveLength(1);
    expect(said[0]?.closest('#connect-cookie-panel')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Connect with cookie' })).toBeEnabled();
  });

  it('Back is disabled while a login is in flight; a late response lands on the code step', async () => {
    let resolve: (v: { status: 'needs_2fa'; flow_id: string }) => void = () => {};
    login.mockReturnValue(new Promise((r) => (resolve = r)));
    render(ConnectApple);
    await toPassword();
    await submitPassword();
    const back = screen.getByRole('button', { name: 'Back' });
    expect(back).toBeDisabled();
    await fireEvent.click(back);
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    resolve({ status: 'needs_2fa', flow_id: 'f' });
    await screen.findByRole('group', { name: 'Verification code' });
  });

  it('Back is disabled while a code is being verified', async () => {
    verify.mockReturnValue(new Promise(() => {}));
    render(ConnectApple);
    await toCode();
    await typeCode('123456');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled());
  });

  it('typed-but-unsubmitted password is cleared when going Back to the Apple ID step', async () => {
    render(ConnectApple);
    await toPassword();
    await fireEvent.input(pwField(), { target: { value: 'half-typed' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await screen.findByLabelText('Apple ID');
    await fireEvent.submit(idField().closest('form') as HTMLFormElement);
    expect(pwField().value).toBe('');
  });

  it('cookie text is cleared when the panel is collapsed', async () => {
    render(ConnectApple);
    const ta = await openCookie();
    await fireEvent.input(ta, { target: { value: 'a=b' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Use a cookie instead' }));
    const again = await openCookie();
    expect(again.value).toBe('');
  });

  it('cookie textarea turns autocorrect off', async () => {
    render(ConnectApple);
    const ta = await openCookie();
    expect(ta.getAttribute('autocorrect')).toBe('off');
  });
});

describe('source rules', () => {
  const read = (f: string) => readFileSync(resolve(import.meta.dirname, '../src/components', f), 'utf8');
  it('no console calls, innerHTML, or authored style attributes', () => {
    for (const f of ['ConnectApple.svelte', 'CodeInput.svelte']) {
      const src = read(f);
      expect(src, f).not.toMatch(/console\./);
      expect(src, f).not.toMatch(/innerHTML|\{@html/);
      expect(src, f).not.toMatch(/\sstyle\s*=/);
    }
  });
});
