import { useEffect, useMemo, useState } from 'react';

/**
 * Hash routes keep shared links working in Telegram and in a browser. Each
 * screen is a page with its own back step; nothing operational lives in a
 * dialog stacked on another screen.
 */
export type DayTab = 'lifts' | 'payments' | 'requests' | 'withdrawals';
export type Route =
  | { name: 'home' }
  | { name: 'days' }
  | { name: 'day'; date: string; tab: DayTab; filter?: string }
  | { name: 'lift'; date: string; time: string; filter?: string }
  | { name: 'order'; date: string; time: string }
  | { name: 'analytics' }
  | { name: 'riders' }
  | { name: 'planning' }
  | { name: 'refunds' }
  | { name: 'audit' }
  | { name: 'myrides' }
  | { name: 'myhistory' };
export type RouteName = Route['name'];

const pages = [
  'home',
  'days',
  'analytics',
  'riders',
  'planning',
  'refunds',
  'audit',
  'myrides',
  'myhistory',
] as const;
// Links shared before the cabinet was reorganised.
const aliases: Record<string, (typeof pages)[number]> = {
  overview: 'home',
  departures: 'days',
};
const isDate = (value: string | undefined): value is string =>
  !!value && /^\d{4}-\d{2}-\d{2}$/.test(value);
const isTime = (value: string | undefined): value is string =>
  !!value && /^\d{1,2}:\d{2}$/.test(value);

export function parseRoute(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(safeDecode);
  const filter = new URLSearchParams(query).get('f') || undefined;
  const [head, date, kind, time, sub] = parts;
  if (head === 'day' && isDate(date)) {
    if (kind === 'lift' && isTime(time))
      return sub === 'order'
        ? { name: 'order', date, time }
        : { name: 'lift', date, time, ...(filter ? { filter } : {}) };
    const tab: DayTab =
      kind === 'payments' || kind === 'requests' || kind === 'withdrawals' ? kind : 'lifts';
    return { name: 'day', date, tab, ...(filter ? { filter } : {}) };
  }
  if (head && head in aliases) return { name: aliases[head] };
  if ((pages as readonly string[]).includes(head)) return { name: head as (typeof pages)[number] };
  return { name: 'home' };
}

export function formatRoute(route: Route): string {
  const query = 'filter' in route && route.filter ? `?f=${encodeURIComponent(route.filter)}` : '';
  switch (route.name) {
    case 'day':
      return `#day/${route.date}${route.tab === 'lifts' ? '' : `/${route.tab}`}${query}`;
    case 'lift':
      return `#day/${route.date}/lift/${route.time}${query}`;
    case 'order':
      return `#day/${route.date}/lift/${route.time}/order`;
    default:
      return `#${route.name}`;
  }
}

/** Where Back leads when the page was opened directly from a link. */
export function parentRoute(route: Route): Route | null {
  switch (route.name) {
    case 'day':
      return { name: 'home' };
    case 'lift':
      return { name: 'day', date: route.date, tab: 'lifts' };
    case 'order':
      return { name: 'lift', date: route.date, time: route.time };
    case 'riders':
    case 'refunds':
    case 'audit':
      return { name: 'home' };
    default:
      return null;
  }
}

function safeDecode(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

type Navigation = 'push' | 'replace' | 'pop';
let lastNavigation: Navigation = 'pop';
const ROUTE_EVENT = 'veloexpress:route';
const depth = () => (history.state as { depth?: number } | null)?.depth ?? 0;

export function navigate(target: Route | string, { replace = false } = {}) {
  const hash = typeof target === 'string' ? target : formatRoute(target);
  if (hash === window.location.hash) return;
  lastNavigation = replace ? 'replace' : 'push';
  if (replace) history.replaceState({ depth: depth() }, '', hash);
  else history.pushState({ depth: depth() + 1 }, '', hash);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

/** History back inside the cabinet; otherwise the logical parent, never out of the app. */
export function goBack(route: Route) {
  if (depth() > 0) {
    history.back();
    return;
  }
  navigate(parentRoute(route) ?? { name: 'home' }, { replace: true });
}

export const takeNavigation = () => {
  const value = lastNavigation;
  lastNavigation = 'pop';
  return value;
};

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener('hashchange', update);
    window.addEventListener('popstate', update);
    window.addEventListener(ROUTE_EVENT, update);
    return () => {
      window.removeEventListener('hashchange', update);
      window.removeEventListener('popstate', update);
      window.removeEventListener(ROUTE_EVENT, update);
    };
  }, []);
  return useMemo(() => parseRoute(hash), [hash]);
}
