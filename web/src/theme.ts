import { useEffect, useState } from 'react';
import { syncTelegramColors, telegram } from './telegram';

export type ThemePreference = 'system' | 'light' | 'dark';
export type ColorTheme = 'light' | 'dark';

const STORAGE_KEY = 'veloexpress-theme';
// Mirrors --bg and --surface in styles/tokens.css for Telegram's own chrome.
const chrome = {
  light: { background: '#f4f3ed', surface: '#fffefa' },
  dark: { background: '#131915', surface: '#1c231e' },
};

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
  if (app) return app.colorScheme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function applyTheme(preference: ThemePreference) {
  const theme = resolveTheme(preference);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document
    .querySelector('meta[name=theme-color]')
    ?.setAttribute('content', chrome[theme].background);
  syncTelegramColors(chrome[theme].background, chrome[theme].surface);
  return theme;
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
