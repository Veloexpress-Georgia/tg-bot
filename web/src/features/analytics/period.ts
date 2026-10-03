import { useSyncExternalStore } from 'react';
import { t } from '../../i18n';
import { num, periodRange } from '../../lib';

export type Period = 'month' | 'year' | 'all' | 'custom';
export interface PeriodState {
  period: Period;
  offset: number;
  custom: { start: string; end: string };
}

// Shared by analytics and riders, kept while the cabinet is open.
let state: PeriodState = { period: 'month', offset: 0, custom: { start: '', end: '' } };
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function usePeriod() {
  const value = useSyncExternalStore(subscribe, () => state);
  const update = (next: Partial<PeriodState>) => {
    state = { ...state, ...next };
    listeners.forEach((listener) => listener());
  };
  return [value, update] as const;
}

/** History ends yesterday: the live day has its own screens. */
export function periodRequest(value: PeriodState, today: string, personal: boolean) {
  const range =
    value.period === 'month' || value.period === 'year'
      ? periodRange(value.period, today, value.offset)
      : value.custom;
  const valid =
    value.period === 'all' ||
    Boolean(range.start && range.end && range.start <= range.end && range.start < today);
  const path =
    value.period === 'all'
      ? `/api/analytics?period=all&personal=${personal}`
      : `/api/analytics?period=custom&start=${range.start}&end=${range.end}&personal=${personal}`;
  return { range, valid, path };
}

export function delta(current: number, previous: number): string {
  if (!previous) return current ? t.analytics.firstResult : t.analytics.noChange;
  const change = ((current - previous) / Math.abs(previous)) * 100;
  return t.analytics.change(`${change > 0 ? '+' : ''}${num(change)}%`);
}
