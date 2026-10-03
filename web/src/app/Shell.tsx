import {
  ArrowDownLeft,
  ArrowLeft,
  BarChart3,
  Bike,
  CalendarDays,
  House,
  LogOut,
  MoreHorizontal,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { isDemo, logout } from '../api';
import { setLocale, t, useLocale } from '../i18n';
import { cx } from '../lib';
import { hasNativeBack, telegram, useTelegramBackButton } from '../telegram';
import type { ThemePreference } from '../theme';
import type { Session } from '../types';
import { Button, Card, List, Row, Section, Segmented, Sheet } from '../ui';
import { Brand } from './Brand';
import { useRefreshAll } from './queries';
import {
  formatRoute,
  goBack,
  navigate,
  parentRoute,
  takeNavigation,
  type Route,
  type RouteName,
} from './router';

interface NavItem {
  route: RouteName;
  label: string;
  icon: LucideIcon;
}
const adminPrimary = (): NavItem[] => [
  { route: 'home', label: t.nav.home, icon: House },
  { route: 'days', label: t.nav.days, icon: Bike },
  { route: 'analytics', label: t.nav.analytics, icon: BarChart3 },
  { route: 'planning', label: t.nav.planning, icon: CalendarDays },
];
const adminSecondary = (): NavItem[] => [
  { route: 'riders', label: t.nav.riders, icon: Users },
  { route: 'refunds', label: t.nav.refunds, icon: ArrowDownLeft },
  { route: 'audit', label: t.nav.audit, icon: ShieldCheck },
];
const riderPrimary = (): NavItem[] => [
  { route: 'myrides', label: t.nav.myrides, icon: Bike },
  { route: 'myhistory', label: t.nav.myhistory, icon: BarChart3 },
];
const nestedRoutes: RouteName[] = ['day', 'lift', 'order'];

export function Shell({
  session,
  route,
  personal,
  theme,
  children,
}: {
  session: Session;
  route: Route;
  personal: boolean;
  theme: { preference: ThemePreference; choose(next: ThemePreference): void };
  children: ReactNode;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const refreshAll = useRefreshAll();
  const nested = nestedRoutes.includes(route.name);
  const hasParent = parentRoute(route) !== null;
  const primary = personal ? riderPrimary() : adminPrimary();
  const secondary = personal ? [] : adminSecondary();
  const active: RouteName = nested ? 'days' : route.name;
  useTelegramBackButton(hasParent, () => goBack(route));
  useScrollRestoration(route);
  useEffect(() => {
    document.documentElement.dataset.nav = nested ? 'hidden' : 'shown';
  }, [nested]);
  const go = (name: RouteName) => {
    setMoreOpen(false);
    navigate({ name } as Route);
  };
  async function refresh() {
    setRefreshing(true);
    try {
      await refreshAll();
    } finally {
      setRefreshing(false);
    }
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand href={personal ? '#myrides' : '#home'} />
        <p className="sidebar-role">{personal ? t.shell.riderRole : t.shell.adminRole}</p>
        <nav className="sidebar-nav" aria-label={t.nav.label}>
          {[...primary, ...secondary].map((item, index) => (
            <button
              key={item.route}
              type="button"
              className={cx(
                'sidebar-link',
                active === item.route && 'active',
                index === primary.length && 'sidebar-gap',
              )}
              aria-current={active === item.route ? 'page' : undefined}
              onClick={() => go(item.route)}
            >
              <item.icon size={19} />
              {item.label}
            </button>
          ))}
        </nav>
        <button
          type="button"
          className="sidebar-link sidebar-foot"
          onClick={() => setMoreOpen(true)}
        >
          <Settings2 size={19} />
          {t.nav.settings}
        </button>
      </aside>
      <div className="shell-main">
        <header className="topbar">
          {hasParent && !hasNativeBack() ? (
            <Button variant="ghost" className="topbar-back" onClick={() => goBack(route)}>
              <ArrowLeft size={20} />
              {t.common.back}
            </Button>
          ) : (
            <Brand compact href={personal ? '#myrides' : '#home'} />
          )}
          <div className="topbar-actions">
            {isDemo && <span className="demo-pill">{t.shell.demo}</span>}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t.shell.refresh}
              disabled={refreshing}
              onClick={refresh}
            >
              <RefreshCw size={18} className={refreshing ? 'spin' : undefined} />
            </Button>
            <button
              type="button"
              className="avatar"
              aria-label={t.shell.account}
              onClick={() => setMoreOpen(true)}
            >
              {session.name.slice(0, 1).toUpperCase()}
            </button>
          </div>
        </header>
        <main className="shell-content">{children}</main>
      </div>
      {!nested && (
        <nav className={cx('bottom-nav', personal && 'bottom-nav-short')} aria-label={t.nav.label}>
          {primary.map((item) => (
            <button
              key={item.route}
              type="button"
              className={cx(active === item.route && 'active')}
              aria-current={active === item.route ? 'page' : undefined}
              onClick={() => go(item.route)}
            >
              <span className="bottom-nav-icon">
                <item.icon size={22} strokeWidth={1.9} />
              </span>
              <span className="bottom-nav-label">{item.label}</span>
            </button>
          ))}
          <button
            type="button"
            className={cx(secondary.some((item) => item.route === active) && 'active')}
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen(true)}
          >
            <span className="bottom-nav-icon">
              <MoreHorizontal size={22} strokeWidth={1.9} />
            </span>
            <span className="bottom-nav-label">{t.nav.more}</span>
          </button>
        </nav>
      )}
      <Sheet
        open={moreOpen}
        onOpenChange={setMoreOpen}
        title={session.name}
        description={personal ? t.shell.riderRole : t.shell.adminRole}
      >
        {secondary.length > 0 && (
          <Card flush className="more-links">
            <List>
              {secondary.map((item) => (
                <Row
                  key={item.route}
                  leading={<item.icon size={19} />}
                  title={item.label}
                  onClick={() => go(item.route)}
                />
              ))}
            </List>
          </Card>
        )}
        <Settings theme={theme} />
        {session.admin && (
          <Button block className="more-action" onClick={() => go(personal ? 'home' : 'myrides')}>
            {personal ? <ShieldCheck size={18} /> : <Bike size={18} />}
            {personal ? t.shell.toAdmin : t.shell.toRider}
          </Button>
        )}
        {!isDemo && !telegram() && (
          <Button block variant="ghost" className="more-action" onClick={() => void logout()}>
            <LogOut size={18} />
            {t.shell.logout}
          </Button>
        )}
      </Sheet>
    </div>
  );
}

function Settings({
  theme,
}: {
  theme: { preference: ThemePreference; choose(next: ThemePreference): void };
}) {
  const locale = useLocale();
  return (
    <>
      <Section title={t.settings.theme} className="settings-section">
        <Segmented
          label={t.settings.theme}
          value={theme.preference}
          onChange={theme.choose}
          options={[
            { value: 'light', label: t.settings.light },
            { value: 'dark', label: t.settings.dark },
            { value: 'system', label: t.settings.auto },
          ]}
        />
        <p className="caption settings-note">{t.settings.autoNote}</p>
      </Section>
      <Section title={t.settings.language} className="settings-section">
        <Segmented
          label={t.settings.language}
          value={locale}
          onChange={setLocale}
          options={[
            { value: 'en', label: 'English' },
            { value: 'ru', label: 'Русский' },
          ]}
        />
      </Section>
    </>
  );
}

/** New pages open at the top; Back returns to where the list was left. */
function useScrollRestoration(route: Route) {
  const positions = useRef(new Map<string, number>());
  const current = useRef('');
  const key = formatRoute(route);
  useLayoutEffect(() => {
    const kind = takeNavigation();
    current.current = key;
    if (kind === 'push') window.scrollTo(0, 0);
    else if (kind === 'pop') window.scrollTo(0, positions.current.get(key) ?? 0);
  }, [key]);
  useEffect(() => {
    const save = () => positions.current.set(current.current, window.scrollY);
    window.addEventListener('scroll', save, { passive: true });
    return () => window.removeEventListener('scroll', save);
  }, []);
}
