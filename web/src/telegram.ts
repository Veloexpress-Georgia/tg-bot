interface TelegramWebApp {
  initData: string;
  ready(): void;
  expand(): void;
  colorScheme: 'light' | 'dark';
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  setBottomBarColor?(color: string): void;
  isVersionAtLeast?(version: string): boolean;
  openTelegramLink(url: string): void;
  onEvent(name: string, callback: () => void): void;
  offEvent(name: string, callback: () => void): void;
  safeAreaInset?: { top: number; bottom: number };
  contentSafeAreaInset?: { top: number; bottom: number };
}
declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}
export const telegram = () => window.Telegram?.WebApp;
export function initTelegram() {
  const app = telegram();
  if (!app?.initData) return () => {};
  app.ready();
  app.expand();
  const update = () => {
    document.documentElement.style.setProperty(
      '--tg-safe-top',
      `${(app.safeAreaInset?.top ?? 0) + (app.contentSafeAreaInset?.top ?? 0)}px`,
    );
    document.documentElement.style.setProperty(
      '--tg-safe-bottom',
      `${(app.safeAreaInset?.bottom ?? 0) + (app.contentSafeAreaInset?.bottom ?? 0)}px`,
    );
    const color = document.documentElement.dataset.theme === 'dark' ? '#171d19' : '#f4f3ed';
    app.setHeaderColor(color);
    app.setBackgroundColor(color);
  };
  update();
  ['themeChanged', 'safeAreaChanged', 'contentSafeAreaChanged'].forEach((name) =>
    app.onEvent(name, update),
  );
  return () =>
    ['themeChanged', 'safeAreaChanged', 'contentSafeAreaChanged'].forEach((name) =>
      app.offEvent(name, update),
    );
}
export function openTelegram(url: string) {
  if (telegram()?.initData) telegram()?.openTelegramLink(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
}
