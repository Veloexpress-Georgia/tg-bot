import { telegram } from './telegram';
import type { CommandSpec, CommandResult, Preview, Session } from './types';
export const isDemo = new URLSearchParams(window.location.search).get('demo') === '1';
let csrf = '';
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (isDemo && (!options?.method || options.method === 'GET'))
    return (await import('./demo')).demoGet(path) as T;
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrf,
      ...options?.headers,
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof payload.detail === 'string' ? payload.detail : 'Проверь поля и попробуй ещё раз.',
    );
  return payload as T;
}
let loginPromise: Promise<Session> | undefined;
export function loadSession(): Promise<Session> {
  if (!loginPromise)
    loginPromise = (async () => {
      const initData = telegram()?.initData;
      if (!isDemo && initData)
        await request('/api/auth/telegram', {
          method: 'POST',
          body: JSON.stringify({ init_data: initData }),
        });
      const session = await request<Session>('/api/session');
      csrf = session.csrf;
      return session;
    })().catch((error) => {
      loginPromise = undefined;
      throw error;
    });
  return loginPromise;
}
export async function previewCommand(spec: CommandSpec): Promise<Preview> {
  if (isDemo)
    return {
      confirmation: 'demo',
      details: spec.action.startsWith('cancel')
        ? 'Будет отменён выбранный выезд.\nУчастники получат уведомление в Telegram.\nДемонстрационная оценка возвратов: 40 GEL.\nФактическое движение денег не выполняется.'
        : 'Опросы будут опубликованы в Telegram по выбранному расписанию. В демонстрации публикация не выполняется.',
      affected: spec.action.startsWith('cancel') ? 8 : undefined,
    };
  return request<Preview>('/api/admin/preview', {
    method: 'POST',
    body: JSON.stringify({
      ...spec,
      request_id: spec.request_id ?? crypto.randomUUID(),
    }),
  });
}
export async function executeCommand(
  spec: CommandSpec,
  confirmation?: string,
  onQueued?: (id: number) => void,
): Promise<CommandResult> {
  if (isDemo)
    return {
      id: 0,
      action: spec.action,
      actor_user_id: 42,
      status: 'complete',
      result: {
        message: 'Демонстрация: действие просмотрено. Рабочие данные не изменены.',
      },
      created_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
    };
  const command = {
    ...spec,
    request_id: spec.request_id ?? crypto.randomUUID(),
  };
  let result = await request<CommandResult>('/api/commands', {
    method: 'POST',
    body: JSON.stringify({ command, confirmation }),
  });
  onQueued?.(result.id);
  for (let i = 0; i < 45 && ['pending', 'running'].includes(result.status); i++) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    result = await request<CommandResult>(`/api/commands/${result.id}`);
  }
  return result;
}
