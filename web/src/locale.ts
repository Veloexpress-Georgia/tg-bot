import { useSyncExternalStore } from 'react';

/** English is the default; Russian is an explicit choice saved on this device. */
export type Locale = 'en' | 'ru';

const STORAGE_KEY = 'veloexpress-locale';
const listeners = new Set<() => void>();

function readStoredLocale(): Locale {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'ru' ? 'ru' : 'en';
  } catch {
    return 'en';
  }
}

let current: Locale = readStoredLocale();

export const currentLocale = () => current;
export const intlLocale = () => (current === 'ru' ? 'ru-RU' : 'en-GB');

export function setCurrentLocale(next: Locale) {
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* Session-only preference if storage is unavailable. */
  }
  current = next;
  listeners.forEach((listener) => listener());
}

export function subscribeLocale(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribeLocale, currentLocale, currentLocale);
}
