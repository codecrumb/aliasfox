import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { ApiError } from '../src/lib/api';

vi.mock('../src/lib/session', () => ({
  sessionStore: { login: vi.fn() }
}));

import { sessionStore } from '../src/lib/session';
import Login from '../src/components/Login.svelte';

const login = vi.mocked(sessionStore.login);

function field(): HTMLInputElement {
  return screen.getByLabelText('Password') as HTMLInputElement;
}

beforeEach(() => {
  login.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Login', () => {
  it('test_login_has_autocomplete_current_password', () => {
    render(Login);
    const input = field();
    expect(input.type).toBe('password');
    expect(input.name).toBe('password');
    expect(input.getAttribute('autocomplete')).toBe('current-password');
    expect(input).toHaveFocus();
    const alert = screen.getByRole('alert');
    expect(input.getAttribute('aria-describedby')).toBe(alert.id);
  });

  it('test_login_submits_password_and_moves_on', async () => {
    login.mockResolvedValue(undefined);
    render(Login);
    await fireEvent.input(field(), { target: { value: 'hunter2' } });
    await fireEvent.submit(field().closest('form') as HTMLFormElement);
    expect(login).toHaveBeenCalledExactlyOnceWith('hunter2');
    await waitFor(() => expect(field().value).toBe(''));
    expect(screen.getByRole('alert')).toHaveTextContent('');
  });

  it('disables the button while submitting', async () => {
    let done: () => void = () => {};
    login.mockReturnValue(new Promise<void>((r) => (done = r)));
    render(Login);
    await fireEvent.input(field(), { target: { value: 'pw' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(screen.getByRole('button', { name: 'Logging in…' })).toBeDisabled();
    done();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled());
  });

  it('test_login_wrong_password_shows_message_and_keeps_field_focus', async () => {
    login.mockRejectedValue(new ApiError(401, 'invalid_credentials'));
    render(Login);
    await fireEvent.input(field(), { target: { value: 'nope' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Incorrect password.')
    );
    expect(field()).toHaveFocus();
    expect(field().value).toBe(''); // password cleared after the failed attempt
  });

  it('test_login_429_shows_retry_after', async () => {
    login.mockRejectedValue(new ApiError(429, 'too_many_attempts', { retryAfter: 3 }));
    render(Login);
    await fireEvent.input(field(), { target: { value: 'x' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Too many attempts. Try again in 3 seconds.'
      )
    );
    expect(screen.getByRole('button', { name: 'Log in' })).toBeDisabled();
  });

  it('countdown ticks down and re-enables the button', async () => {
    vi.useFakeTimers();
    login.mockRejectedValue(new ApiError(429, 'rate_limited', { retryAfter: 2 }));
    render(Login);
    await fireEvent.input(field(), { target: { value: 'x' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole('alert')).toHaveTextContent('Try again in 2 seconds.');
    await vi.advanceTimersByTimeAsync(1000);
    expect(screen.getByRole('alert')).toHaveTextContent('Try again in 1 second.');
    expect(screen.getByRole('button', { name: 'Log in' })).toBeDisabled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveTextContent('');
  });

  it('network and unknown errors show a generic message, never server text', async () => {
    login.mockRejectedValue(
      new ApiError(500, 'internal_error', { serverMessage: 'SECRET stack trace' })
    );
    render(Login);
    await fireEvent.input(field(), { target: { value: 'x' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        "Can't reach AliasFox. Check your connection and try again."
      )
    );
    expect(document.body.textContent).not.toContain('SECRET');
  });
});
