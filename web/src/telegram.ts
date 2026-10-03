import { useEffect, useRef } from 'react';

interface TelegramBackButton {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(callback: () => void): void;
  offClick(callback: () => void): void;
}
interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: { user?: { id: number } };
  version: string;
  colorScheme: 'light' | 'dark';
  ready(): void;
  expand(): void;
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  setBottomBarColor?(color: string): void;
  isVersionAtLeast(version: string): boolean;
  openTelegramLink(url: string): void;
  onEvent(name: string, callback: () => void): void;
  offEvent(name: string, callback: () => void): void;
  safeAreaInset?: { top: number; bottom: number };
  contentSafeAreaInset?: { top: number; bottom: number };
  BackButton?: TelegramBackButton;
  HapticFeedback?: {
    impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void;
    notificationOccurred(type: 'error' | 'success' | 'warning'): void;
    selectionChanged(): void;
  };
}
declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

/** The Mini App bridge, only when the page was really opened from Telegram. */
export function telegram(): TelegramWebApp | undefined {
  const app = window.Telegram?.WebApp;
  return app?.initData ? app : undefined;
}
const supports = (version: string) => !!telegram()?.isVersionAtLeast?.(version);

const insetEvents = ['safeAreaChanged', 'contentSafeAreaChanged', 'viewportChanged'];
function syncInsets() {
  const app = telegram();
  if (!app) return;
  const root = document.documentElement.style;
  root.setProperty(
    '--tg-safe-top',
    `${(app.safeAreaInset?.top ?? 0) + (app.contentSafeAreaInset?.top ?? 0)}px`,
  );
  root.setProperty(
    '--tg-safe-bottom',
    `${(app.safeAreaInset?.bottom ?? 0) + (app.contentSafeAreaInset?.bottom ?? 0)}px`,
  );
}

/**
 * Runs before React renders. Telegram keeps its own loading placeholder until
 * ready() is called, so it must not wait for the session or the first query.
 */
export function bootTelegram() {
  const app = telegram();
  if (!app) return;
  document.documentElement.dataset.telegram = 'true';
  app.ready();
  app.expand();
  syncInsets();
  insetEvents.forEach((name) => app.onEvent(name, syncInsets));
}

export function syncTelegramColors(background: string, bottomBar: string) {
  const app = telegram();
  if (!app) return;
  app.setHeaderColor(background);
  app.setBackgroundColor(background);
  if (supports('7.10')) app.setBottomBarColor?.(bottomBar);
}

/** Telegram's own header back button, when the client has one. */
export const hasNativeBack = () => supports('6.1') && !!telegram()?.BackButton;

export function useTelegramBackButton(visible: boolean, onBack: () => void) {
  const handler = useRef(onBack);
  handler.current = onBack;
  useEffect(() => {
    const button = hasNativeBack() ? telegram()?.BackButton : undefined;
    if (!button) return;
    if (!visible) {
      button.hide();
      return;
    }
    const click = () => handler.current();
    button.onClick(click);
    button.show();
    return () => button.offClick(click);
  }, [visible]);
}

export function haptic(kind: 'tap' | 'success' | 'error' | 'warning' | 'select') {
  if (!supports('6.1')) return;
  const feedback = telegram()?.HapticFeedback;
  if (!feedback) return;
  if (kind === 'tap') feedback.impactOccurred('light');
  else if (kind === 'select') feedback.selectionChanged();
  else feedback.notificationOccurred(kind);
}

export function openTelegram(url: string) {
  const app = telegram();
  if (app) app.openTelegramLink(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
}

export const telegramUserId = () => telegram()?.initDataUnsafe?.user?.id;
