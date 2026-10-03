import { lazy, Suspense, useEffect, useState } from 'react';
import { CommandProvider } from './app/commands';
import { Brand } from './app/Brand';
import { useSession } from './app/queries';
import { navigate, useRoute, type Route } from './app/router';
import { Shell } from './app/Shell';
import { DayPage } from './features/days/DayPage';
import { DaysPage } from './features/days/DaysPage';
import { LiftPage } from './features/days/LiftPage';
import { OrderPage } from './features/days/OrderPage';
import { HomePage } from './features/home/HomePage';
import { LoginPage } from './features/login/LoginPage';
import { PlanningPage } from './features/planning/PlanningPage';
import { AuditPage, RefundsPage } from './features/reports/ReportPages';
import { MyRidesPage } from './features/rides/MyRidesPage';
import { t } from './i18n';
import { useTheme } from './theme';
import type { Session } from './types';
import { PageSkeleton, Spinner } from './ui';

// Charts are the heaviest part of the bundle; the day-to-day screens do not wait for them.
const AnalyticsPage = lazy(() =>
  import('./features/analytics/AnalyticsPage').then((m) => ({ default: m.AnalyticsPage })),
);
const RidersPage = lazy(() =>
  import('./features/analytics/RidersPage').then((m) => ({ default: m.RidersPage })),
);

export default function App() {
  const session = useSession();
  const route = useRoute();
  const theme = useTheme();
  if (session.isPending) return <BootScreen />;
  if (!session.data)
    return <LoginPage error={session.error} retry={() => void session.refetch()} />;
  const riderRoute = route.name === 'myrides' || route.name === 'myhistory';
  const allowed = session.data.admin || riderRoute;
  const current: Route = allowed ? route : { name: 'myrides' };
  return (
    <CommandProvider userId={session.data.user_id}>
      <RouteGuard allowed={allowed} />
      <Shell
        session={session.data}
        route={current}
        personal={riderRoute || !session.data.admin}
        theme={theme}
      >
        <Suspense fallback={<PageSkeletonPage />}>
          <Screen key={screenKey(current)} route={current} session={session.data} />
        </Suspense>
      </Shell>
    </CommandProvider>
  );
}

function RouteGuard({ allowed }: { allowed: boolean }) {
  useEffect(() => {
    if (!allowed) navigate({ name: 'myrides' }, { replace: true });
  }, [allowed]);
  return null;
}

const screenKey = (route: Route) =>
  route.name === 'day'
    ? `day:${route.date}`
    : route.name === 'lift' || route.name === 'order'
      ? `${route.name}:${route.date}:${route.time}`
      : route.name;

function Screen({ route, session }: { route: Route; session: Session }) {
  switch (route.name) {
    case 'home':
      return <HomePage session={session} />;
    case 'days':
      return <DaysPage session={session} />;
    case 'day':
      return <DayPage route={route} session={session} />;
    case 'lift':
      return <LiftPage route={route} session={session} />;
    case 'order':
      return <OrderPage route={route} session={session} />;
    case 'analytics':
      return <AnalyticsPage session={session} />;
    case 'riders':
      return <RidersPage session={session} />;
    case 'planning':
      return <PlanningPage session={session} />;
    case 'refunds':
      return <RefundsPage />;
    case 'audit':
      return <AuditPage session={session} />;
    case 'myrides':
      return <MyRidesPage />;
    case 'myhistory':
      return <AnalyticsPage session={session} personal />;
  }
}

function PageSkeletonPage() {
  return (
    <div className="page">
      <PageSkeleton />
    </div>
  );
}

function BootScreen() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 6000);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div className="boot" role="status">
      <Brand />
      <Spinner size={22} />
      <p>{slow ? t.boot.slow : t.boot.opening}</p>
    </div>
  );
}
