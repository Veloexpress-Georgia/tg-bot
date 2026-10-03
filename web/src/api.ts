import { proposeOrder } from './booking-order';
import { t } from './i18n';
import { telegram, telegramUserId } from './telegram';
import type { BookingOrder, CommandResult, CommandSpec, Preview, Session } from './types';

export const isDemo = new URLSearchParams(window.location.search).get('demo') === '1';
let csrf = '';
const TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
/** The request may or may not have reached the server. */
export class NetworkError extends Error {}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (isDemo && (!options?.method || options.method === 'GET'))
    return (await import('./demo')).demoGet(path) as T;
  // A request that never answers must end in an error the screen can retry,
  // not in a spinner that waits forever.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    let response: Response;
    try {
      response = await fetch(path, {
        ...options,
        signal: controller.signal,
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrf,
          ...options?.headers,
        },
      });
    } catch {
      throw new NetworkError(controller.signal.aborted ? t.errors.timeout : t.errors.offline);
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new ApiError(
        response.status,
        typeof payload.detail === 'string' ? payload.detail : t.errors.invalid,
      );
    return payload as T;
  } finally {
    clearTimeout(timer);
  }
}

let loginPromise: Promise<Session> | undefined;
export function loadSession(): Promise<Session> {
  if (!loginPromise)
    loginPromise = (async () => {
      const initData = telegram()?.initData;
      if (!isDemo && initData) {
        try {
          await request('/api/auth/telegram', {
            method: 'POST',
            body: JSON.stringify({ init_data: initData }),
          });
        } catch (error) {
          // Telegram replays the original launch data when the Mini App reloads.
          // It expires after ten minutes; the session it created lasts longer.
          if (!(error instanceof ApiError && error.status === 401)) throw error;
          const existing = await request<Session>('/api/session').catch(() => null);
          if (!existing || existing.user_id !== telegramUserId()) throw error;
          csrf = existing.csrf;
          return existing;
        }
      }
      const session = await request<Session>('/api/session');
      csrf = session.csrf;
      return session;
    })().catch((error) => {
      loginPromise = undefined;
      throw error;
    });
  return loginPromise;
}

export async function logout() {
  await request('/api/auth/logout', { method: 'POST' });
  window.location.assign('/');
}

export async function previewCommand(spec: CommandSpec): Promise<Preview> {
  if (isDemo && spec.action === 'booking_order') {
    const order = await request<BookingOrder>(
      `/api/admin/days/${spec.service_date}/lifts/${encodeURIComponent(spec.lift_time!)}/order`,
    );
    return {
      ...proposeOrder(order, spec.ordered_user_ids ?? order.riders.map((r) => r.user_id)),
      confirmation: 'demo',
      details: t.demo.noChanges,
    };
  }
  if (isDemo)
    return {
      confirmation: 'demo',
      details: spec.action.startsWith('cancel') ? t.demo.cancelPreview : t.demo.publishPreview,
      affected: spec.action.startsWith('cancel') ? 8 : undefined,
    };
  return request<Preview>('/api/admin/preview', {
    method: 'POST',
    body: JSON.stringify(withRequestId(spec)),
  });
}

/** Queues the command; the bot executes it and the caller follows its status. */
export async function submitCommand(
  spec: CommandSpec,
  confirmation?: string,
): Promise<CommandResult> {
  if (isDemo)
    return {
      id: 0,
      action: spec.action,
      actor_user_id: 42,
      status: 'complete',
      result: { message: t.demo.noChanges },
      created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
    };
  return request<CommandResult>('/api/commands', {
    method: 'POST',
    body: JSON.stringify({ command: withRequestId(spec), confirmation }),
  });
}

const withRequestId = (spec: CommandSpec) => ({
  ...spec,
  request_id: spec.request_id ?? crypto.randomUUID(),
});

export const getCommand = (id: number) => request<CommandResult>(`/api/commands/${id}`);
