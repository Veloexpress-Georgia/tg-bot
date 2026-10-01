import { useEffect, useState } from 'react';
import { telegram } from './telegram';
export type ThemePreference = 'system' | 'light' | 'dark';
export type ColorTheme = 'light' | 'dark';
const STORAGE_KEY = 'veloexpress-theme';
export function readThemePreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}
export function resolveTheme(preference: ThemePreference): ColorTheme {
  if (preference !== 'system') return preference;
  const app = telegram();
  if (app?.initData) return app.colorScheme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
export function applyTheme(preference: ThemePreference) {
  const theme = resolveTheme(preference);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document
    .querySelector('meta[name=theme-color]')
    ?.setAttribute('content', theme === 'dark' ? '#171d19' : '#f4f3ed');
  syncTelegramColors(theme);
  return theme;
}
export function syncTelegramColors(theme: ColorTheme) {
  const app = telegram();
  if (!app?.initData) return;
  const color = theme === 'dark' ? '#171d19' : '#f4f3ed';
  app.setHeaderColor(color);
  app.setBackgroundColor(color);
  if (app.isVersionAtLeast?.('7.10')) {
    app.setBottomBarColor?.(theme === 'dark' ? '#212a23' : '#fffefa');
  }
}
export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);
  const [theme, setTheme] = useState<ColorTheme>(() => resolveTheme(preference));
  useEffect(() => {
    const update = () => setTheme(applyTheme(preference));
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const app = telegram();
    update();
    media.addEventListener('change', update);
    app?.onEvent('themeChanged', update);
    return () => {
      media.removeEventListener('change', update);
      app?.offEvent('themeChanged', update);
    };
  }, [preference]);
  function choose(next: ThemePreference) {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* Session-only preference if storage is unavailable. */
    }
    setPreference(next);
    setTheme(applyTheme(next));
  }
  return { preference, theme, choose };
}
