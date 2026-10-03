import { afterEach, describe, expect, it, vi } from 'vitest';
import { delta } from './features/analytics/period';
import { setLocale, t } from './i18n';
import { en } from './locales/en';
import { ru } from './locales/ru';

vi.stubGlobal('document', { documentElement: {} });
afterEach(() => setLocale('en'));

function shape(value: unknown): unknown {
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'function') return 'function';
  if (value && typeof value === 'object')
    return Object.fromEntries(Object.entries(value).map(([key, inner]) => [key, shape(inner)]));
  return typeof value;
}

describe('cabinet language', () => {
  it('starts in English', () => {
    expect(t.nav.home).toBe('Home');
    expect(t.day.liftsRunning(3, 5)).toBe('3 of 5 lifts running');
    expect(t.day.riders(1)).toBe('1 rider');
  });
  it('offers Russian with every English key', () => {
    expect(shape(ru)).toEqual(shape(en));
  });
  it('uses Russian plural forms', () => {
    setLocale('ru');
    expect(t.day.riders(1)).toBe('1 райдер');
    expect(t.day.riders(3)).toBe('3 райдера');
    expect(t.day.riders(5)).toBe('5 райдеров');
    expect(t.day.liftsRunning(1, 5)).toBe('1 из 5 выездов набрал минимум');
    expect(t.day.liftsRunning(3, 21)).toBe('3 из 21 выезда набрали минимум');
  });
  it('never shows infinite growth', () => {
    expect(delta(50, 0)).toBe('First result');
    expect(delta(0, 0)).toBe('No change');
    expect(delta(100, 50)).toContain('+100%');
    setLocale('ru');
    expect(delta(100, 50)).toBe('+100% к прошлому периоду');
  });
});
