import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, X } from 'lucide-react';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, getCommand, previewCommand, submitCommand } from '../api';
import { OrderConsequences } from '../features/days/OrderConsequences';
import { t } from '../i18n';
import { cx, plain } from '../lib';
import { haptic } from '../telegram';
import type { BookingOrderProposal, CommandResult, CommandSpec, Preview } from '../types';
import { Button, Sheet, Spinner } from '../ui';
import { keys } from './queries';

export interface ActOptions {
  /** Runs once the bot has finished the command successfully. */
  onComplete?(result: CommandResult): void;
}
interface Commands {
  act(spec: CommandSpec, options?: ActOptions): void;
  busy: boolean;
}
const CommandContext = createContext<Commands>({ act: () => undefined, busy: false });
export const useCommands = () => useContext(CommandContext);

const CONFIRMED = new Set<CommandSpec['action']>([
  'cancel_day',
  'cancel_lift',
  'post',
  'extra',
  'booking_order',
]);
const FINISHED = new Set<CommandResult['status']>(['complete', 'failed', 'review']);
// The worker polls every second; a request still pending after this is waiting
// for a bot that is not running, not for a slow one.
const BOT_SILENT_MS = 20_000;
const FORGET_AFTER_MS = 10 * 60_000;

interface Tracked {
  id: number;
  spec?: CommandSpec;
  since: number;
}
const storageKey = (userId: number) => `veloexpress-command-${userId}`;
function restoreTracked(userId: number): Tracked | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(userId)) ?? 'null') as
      Tracked | number | null;
    if (typeof value === 'number') return { id: value, since: Date.now() };
    return value && Date.now() - value.since < FORGET_AFTER_MS ? value : null;
  } catch {
    return null;
  }
}

/**
 * Web writes are durable requests the bot executes. One runs at a time; its
 * progress is always visible and never blocks the cabinet indefinitely.
 */
export function CommandProvider({ userId, children }: { userId: number; children: ReactNode }) {
  const client = useQueryClient();
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirm, setConfirm] = useState<{
    spec: CommandSpec;
    preview: Preview;
    options?: ActOptions;
  } | null>(null);
  const [warning, setWarning] = useState<{
    spec: CommandSpec;
    text: string;
    options?: ActOptions;
  } | null>(null);
  const [uncertain, setUncertain] = useState<{
    spec: CommandSpec;
    confirmation?: string;
    options?: ActOptions;
  } | null>(null);
  const [tracked, setTracked] = useState<Tracked | null>(() => restoreTracked(userId));
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const callbacks = useRef(new Map<number, ActOptions | undefined>());
  const busy = previewing || submitting || !!tracked;
  // A second tap can arrive before React re-renders with busy = true.
  const lock = useRef(false);
  useEffect(() => {
    if (!busy) lock.current = false;
  }, [busy]);

  function track(next: Tracked | null) {
    setTracked(next);
    try {
      if (next) sessionStorage.setItem(storageKey(userId), JSON.stringify(next));
      else sessionStorage.removeItem(storageKey(userId));
    } catch {
      /* Tracking still works for this page view. */
    }
  }
  function notify(text: string, error = false) {
    setToast({ text, error });
    haptic(error ? 'error' : 'success');
  }

  const status = useQuery({
    queryKey: keys.command(tracked?.id ?? 0),
    queryFn: () => getCommand(tracked!.id),
    enabled: !!tracked,
    refetchInterval: 1000,
    retry: (count, error) => !(error instanceof ApiError) && count < 3,
    gcTime: 0,
  });

  function finish(result: CommandResult, spec?: CommandSpec, options?: ActOptions) {
    track(null);
    void client.invalidateQueries({
      predicate: (query) => !['session', 'command'].includes(String(query.queryKey[0])),
    });
    if (result.result?.needs_confirmation && spec) {
      setWarning({ spec, text: result.result.message, options });
      haptic('warning');
    } else if (result.status === 'complete') {
      const message = result.result?.message;
      notify(message && message !== 'Saved' ? plain(message) : t.cmd.saved);
      options?.onComplete?.(result);
    } else
      notify(
        plain(result.result?.message) || (result.status === 'review' ? t.cmd.review : t.cmd.failed),
        true,
      );
  }

  useEffect(() => {
    if (!tracked || status.data?.id !== tracked.id || !FINISHED.has(status.data.status)) return;
    const options = callbacks.current.get(tracked.id);
    callbacks.current.delete(tracked.id);
    finish(status.data, tracked.spec, options);
  }, [status.data]);

  useEffect(() => {
    // The request belongs to someone else, a reset database or an expired session.
    if (!tracked || !(status.error instanceof ApiError)) return;
    track(null);
    notify(t.cmd.lost(tracked.id), true);
  }, [status.error]);

  useEffect(() => {
    if (!tracked) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [tracked]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.error ? 10_000 : 4_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function submit(spec: CommandSpec, confirmation?: string, options?: ActOptions) {
    setSubmitting(true);
    setUncertain(null);
    try {
      const result = await submitCommand(spec, confirmation);
      setConfirm(null);
      if (FINISHED.has(result.status)) finish(result, spec, options);
      else {
        callbacks.current.set(result.id, options);
        track({ id: result.id, spec, since: Date.now() });
      }
    } catch (error) {
      setConfirm(null);
      if (error instanceof ApiError) notify(error.message, true);
      // Unknown outcome: retrying with the same request id returns the original.
      else setUncertain({ spec, confirmation, options });
    } finally {
      setSubmitting(false);
    }
  }

  async function act(input: CommandSpec, options?: ActOptions) {
    if (busy || lock.current) return;
    lock.current = true;
    haptic('tap');
    const spec = { ...input, request_id: input.request_id ?? crypto.randomUUID() };
    if (!CONFIRMED.has(spec.action)) {
      void submit(spec, undefined, options);
      return;
    }
    setPreviewing(true);
    try {
      setConfirm({ spec, preview: await previewCommand(spec), options });
    } catch (error) {
      notify(error instanceof Error ? error.message : t.cmd.previewFailed, true);
    } finally {
      setPreviewing(false);
    }
  }

  const elapsed = tracked ? Math.max(0, Math.round((now - tracked.since) / 1000)) : 0;
  const running = status.data?.status === 'running';
  const silent = !!tracked && !running && now - tracked.since > BOT_SILENT_MS;
  const destructive = !!confirm?.spec.action.startsWith('cancel');

  return (
    <CommandContext.Provider value={{ act, busy }}>
      {children}
      <div className="floating" aria-live="polite">
        {busy && (
          <div className={cx('command-bar', silent && 'command-bar-warn')} role="status">
            {silent ? <CircleAlert size={18} /> : <Spinner size={18} />}
            <div className="command-bar-text">
              <strong>
                {previewing
                  ? t.cmd.preparing
                  : submitting
                    ? t.cmd.sending
                    : tracked?.spec
                      ? t.actions[tracked.spec.action]
                      : t.cmd.request(tracked?.id ?? 0)}
              </strong>
              {tracked && (
                <span>
                  {silent
                    ? t.cmd.botSilent
                    : running
                      ? t.cmd.running(elapsed)
                      : t.cmd.queued(elapsed)}
                </span>
              )}
            </div>
            {silent && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  track(null);
                  notify(t.cmd.stillQueued);
                }}
              >
                {t.cmd.hide}
              </Button>
            )}
          </div>
        )}
        {uncertain && !busy && (
          <div className="command-bar command-bar-warn" role="alert">
            <CircleAlert size={18} />
            <div className="command-bar-text">
              <strong>{t.cmd.uncertainTitle}</strong>
              <span>{t.cmd.uncertainText}</span>
            </div>
            <Button
              size="sm"
              onClick={() => submit(uncertain.spec, uncertain.confirmation, uncertain.options)}
            >
              {t.cmd.checkAgain}
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label={t.common.close}
              onClick={() => setUncertain(null)}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        {toast && (
          <div
            className={cx('toast', toast.error && 'toast-error')}
            role={toast.error ? 'alert' : 'status'}
          >
            <span>{toast.text}</span>
            <button type="button" aria-label={t.common.close} onClick={() => setToast(null)}>
              <X size={16} />
            </button>
          </div>
        )}
      </div>
      <Sheet
        open={!!confirm}
        onOpenChange={(open) => {
          if (!open && !submitting) setConfirm(null);
        }}
        title={confirm ? t.confirm.title[confirm.spec.action as keyof typeof t.confirm.title] : ''}
        description={t.confirm.description}
        footer={
          confirm && (
            <>
              <Button disabled={submitting} onClick={() => setConfirm(null)}>
                {t.common.back}
              </Button>
              <Button
                variant={destructive ? 'danger' : 'primary'}
                disabled={submitting}
                onClick={() => submit(confirm.spec, confirm.preview.confirmation, confirm.options)}
              >
                {submitting ? t.cmd.sending : t.confirm.confirm}
              </Button>
            </>
          )
        }
      >
        {confirm &&
          (confirm.spec.action === 'booking_order' && confirm.preview.ordered_user_ids ? (
            <OrderConsequences proposal={confirm.preview as BookingOrderProposal} detailed />
          ) : (
            plain(confirm.preview.details) && (
              <pre className="report">{plain(confirm.preview.details)}</pre>
            )
          ))}
        {confirm?.preview.affected !== undefined && (
          <p className="caption confirm-affected">{t.confirm.affected(confirm.preview.affected)}</p>
        )}
      </Sheet>
      <Sheet
        open={!!warning}
        onOpenChange={(open) => {
          if (!open) setWarning(null);
        }}
        title={t.payments.checkTitle}
        description={t.payments.checkDescription}
        footer={
          warning && (
            <>
              <Button onClick={() => setWarning(null)}>{t.common.back}</Button>
              <Button
                variant="primary"
                disabled={busy}
                onClick={() => {
                  const { spec, options } = warning;
                  setWarning(null);
                  act({ ...spec, request_id: crypto.randomUUID(), acknowledged: true }, options);
                }}
              >
                {t.payments.recordAnyway}
              </Button>
            </>
          )
        }
      >
        {warning && <pre className="report">{plain(warning.text)}</pre>}
      </Sheet>
    </CommandContext.Provider>
  );
}
