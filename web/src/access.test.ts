import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { en } from './locales/en';

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers });

beforeEach(() => vi.resetModules());
afterEach(() => vi.unstubAllGlobals());

describe('cabinet access for chat members only', () => {
  it('explains a members-only refusal in the cabinet language', async () => {
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          json(403, { detail: 'Only members…' }, { 'X-Error-Code': 'not_member' }),
        ),
    );
    const { ApiError, request } = await import('./api');
    const error = await request('/api/my-days').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).code).toBe('not_member');
    expect((error as Error).message).toBe(en.login.membersOnly);
  });

  it('shows the refusal, not "reopen", when a Mini App sign-in is refused', async () => {
    vi.stubGlobal('window', {
      location: { search: '' },
      Telegram: { WebApp: { initData: 'signed', initDataUnsafe: { user: { id: 42 } } } },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json(403, { detail: 'x' }, { 'X-Error-Code': 'not_member' })),
    );
    const { loadSession } = await import('./api');
    await expect(loadSession()).rejects.toMatchObject({ code: 'not_member', status: 403 });
  });

  it('keeps a refusal behind an expired launch replay', async () => {
    vi.stubGlobal('window', {
      location: { search: '' },
      Telegram: { WebApp: { initData: 'signed', initDataUnsafe: { user: { id: 42 } } } },
    });
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(json(401, { detail: 'expired' }))
        .mockResolvedValueOnce(json(403, { detail: 'x' }, { 'X-Error-Code': 'not_member' })),
    );
    const { loadSession } = await import('./api');
    await expect(loadSession()).rejects.toMatchObject({ code: 'not_member' });
  });

  it('explains a request limit', async () => {
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(429, { detail: 'Too many' })));
    const { request } = await import('./api');
    await expect(request('/api/commands', { method: 'POST' })).rejects.toMatchObject({
      status: 429,
      message: en.errors.tooMany,
    });
  });

  it('reads a browser sign-in refusal from the address', async () => {
    vi.stubGlobal('window', { location: { search: '' } });
    const { loginRefusal } = await import('./api');
    expect(loginRefusal('?login_error=not_member')).toBe(en.login.membersOnly);
    expect(loginRefusal('?login_error=membership_unavailable')).toBe(
      en.login.membershipUnavailable,
    );
    expect(loginRefusal('?login_error=<b>')).toBeUndefined();
    expect(loginRefusal('')).toBeUndefined();
  });
});
