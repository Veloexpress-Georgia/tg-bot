import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDownLeft,
  ArrowUpRight,
  BarChart3,
  Bike,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  LayoutDashboard,
  Loader2,
  LogOut,
  MoreHorizontal,
  Moon,
  Sun,
  Monitor,
  RefreshCw,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { ApiError, executeCommand, isDemo, loadSession, previewCommand, request } from './api';
import { dateLabel, money, periodRange, plain } from './lib';
import { initTelegram } from './telegram';
import { useTheme } from './theme';
import { Brand } from './components/Brand';
import type {
  Analytics,
  CommandResult,
  CommandSpec,
  LiveDay,
  MyDays,
  Planning,
  Preview,
  RefundReport,
} from './types';
import { Button } from './components/ui/button';
import { Dialog } from './components/ui/dialog';
const Dashboard = lazy(() =>
  import('./components/Dashboard').then((m) => ({ default: m.Dashboard })),
);
const RidersTable = lazy(() =>
  import('./components/Dashboard').then((m) => ({ default: m.RidersTable })),
);
import {
  AuditView,
  DayDialog,
  Departures,
  PlanningView,
  RefundsView,
} from './components/Operations';
import { MyRides } from './components/MyRides';

type Tab =
  'overview' | 'analytics' | 'departures' | 'riders' | 'planning' | 'refunds' | 'audit' | 'myrides';
type Period = 'month' | 'year' | 'all' | 'custom';
const navigation = [
  { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
  { id: 'analytics', label: 'Аналитика', icon: BarChart3 },
  { id: 'departures', label: 'Выезды', icon: Bike },
  { id: 'riders', label: 'Участники', icon: Users },
  { id: 'planning', label: 'Планирование', icon: CalendarDays },
  { id: 'refunds', label: 'Возвраты', icon: ArrowDownLeft },
  { id: 'audit', label: 'Журнал действий', icon: ShieldCheck },
] as const;
const titles: Record<Tab, { title: string; subtitle: string; eyebrow: string }> = {
  overview: {
    title: 'Сезон в движении',
    subtitle: 'Все выезды и оплаты складываются в общую картину.',
    eyebrow: 'ОБЩАЯ КАРТИНА',
  },
  analytics: {
    title: 'За цифрами — поездки',
    subtitle: 'Посмотри, как меняются загрузка, сборы и участие.',
    eyebrow: 'АНАЛИТИКА',
  },
  departures: {
    title: 'Всё готово к выезду?',
    subtitle: 'Ближайшие дни, списки участников и инструменты админа.',
    eyebrow: 'ВЫЕЗДЫ',
  },
  riders: {
    title: 'Наше сообщество',
    subtitle: 'Кто возвращается, кто присоединился и как часто мы ездим.',
    eyebrow: 'УЧАСТНИКИ',
  },
  planning: {
    title: 'Планы на выходные',
    subtitle: 'Расписание и условия выездов — под твоим контролем.',
    eyebrow: 'ПЛАНИРОВАНИЕ',
  },
  refunds: {
    title: 'Сохранённые оценки',
    subtitle: 'Суммы, показанные админам при отмене выезда или дня.',
    eyebrow: 'ВОЗВРАТЫ',
  },
  audit: {
    title: 'Что изменилось',
    subtitle: 'Прозрачная история действий из кабинета.',
    eyebrow: 'ЖУРНАЛ ДЕЙСТВИЙ',
  },
  myrides: {
    title: 'Мои поездки',
    subtitle: 'Места, гости и оплаты — всё рядом.',
    eyebrow: 'ЛИЧНЫЙ КАБИНЕТ',
  },
};
function initialTab(): Tab {
  const hash = window.location.hash.slice(1);
  return hash in titles ? (hash as Tab) : 'overview';
}
export default function App() {
  const queryClient = useQueryClient();
  const { preference, theme, choose } = useTheme();
  const session = useQuery({
    queryKey: ['session'],
    queryFn: loadSession,
    retry: false,
  });
  const [tab, setTab] = useState<Tab>(initialTab),
    [mobileMenu, setMobileMenu] = useState(false),
    [personal, setPersonal] = useState(() => initialTab() === 'myrides');
  const [period, setPeriod] = useState<Period>('month'),
    [offset, setOffset] = useState(0),
    [custom, setCustom] = useState({ start: '', end: '' });
  const [day, setDay] = useState<string | null>(null),
    [personalDay, setPersonalDay] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{
      spec: CommandSpec;
      preview: Preview;
    } | null>(null),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null),
    [warning, setWarning] = useState<{
      spec: CommandSpec;
      text: string;
    } | null>(null);
  const [ambiguous, setAmbiguous] = useState<CommandSpec | null>(null);
  const pendingSpec = useRef<CommandSpec | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.error ? 12000 : 7000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileMenu(false);
    };
    window.addEventListener('keydown', onEscape);
    return () => window.removeEventListener('keydown', onEscape);
  }, []);
  useEffect(() => initTelegram(), []);
  useEffect(() => {
    const handler = () => {
      const next = initialTab();
      setTab(next);
      if (next === 'myrides') setPersonal(true);
    };
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);
  useEffect(() => {
    if (!session.data) return;
    if (!session.data.admin) {
      setPersonal(true);
      setTab('myrides');
    }
    const saved = sessionStorage.getItem(`veloexpress-command-${session.data.user_id}`);
    if (saved && Number(saved) > 0) setPendingId(Number(saved));
  }, [session.data]);
  const isPersonal = personal || !session.data?.admin;
  const effectiveTab = isPersonal && !['myrides', 'analytics'].includes(tab) ? 'myrides' : tab;
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [effectiveTab]);
  const today = session.data?.today ?? '2026-10-01';
  const range =
    period === 'month' || period === 'year' ? periodRange(period, today, offset) : custom;
  const validRange =
    period === 'all' ||
    Boolean(range.start && range.end && range.start <= range.end && range.start < today);
  const analyticsPath =
    period === 'all'
      ? `/api/analytics?period=all&personal=${isPersonal}`
      : `/api/analytics?period=custom&start=${range.start}&end=${range.end}&personal=${isPersonal}`;
  const analytics = useQuery({
    queryKey: ['analytics', analyticsPath],
    queryFn: () => request<Analytics>(analyticsPath),
    enabled:
      !!session.data && validRange && ['overview', 'analytics', 'riders'].includes(effectiveTab),
  });
  const days = useQuery({
    queryKey: ['days'],
    queryFn: () => request<LiveDay[]>('/api/admin/days'),
    enabled: !!session.data?.admin && !isPersonal,
    refetchInterval: 30_000,
  });
  const planning = useQuery({
    queryKey: ['planning'],
    queryFn: () => request<Planning>('/api/admin/planning'),
    enabled: !!session.data?.admin && effectiveTab === 'planning',
  });
  const myDays = useQuery({
    queryKey: ['mydays'],
    queryFn: () => request<MyDays>('/api/my-days'),
    enabled: !!session.data && effectiveTab === 'myrides',
    refetchInterval: 30_000,
  });
  const refunds = useQuery({
    queryKey: ['refunds'],
    queryFn: () => request<RefundReport[]>('/api/admin/refunds'),
    enabled: !!session.data?.admin && effectiveTab === 'refunds',
  });
  const audit = useQuery({
    queryKey: ['audit'],
    queryFn: () => request<CommandResult[]>('/api/admin/audit'),
    enabled: !!session.data?.admin && effectiveTab === 'audit',
    refetchInterval: 5000,
  });
  const refresh = () => {
    queryClient.invalidateQueries({
      predicate: (query) => query.queryKey[0] !== 'session',
    });
  };
  const mutation = useMutation({
    mutationFn: ({ spec, confirmation }: { spec: CommandSpec; confirmation?: string }) =>
      executeCommand(spec, confirmation, (id) => {
        setPendingId(id);
        sessionStorage.setItem(`veloexpress-command-${session.data!.user_id}`, String(id));
      }),
    onSuccess: (result) => {
      setConfirm(null);
      if (['complete', 'failed', 'review'].includes(result.status)) finish(result);
      else
        setToast({
          text: `Запрос #${result.id} сохранён. Бот выполнит его и обновит результат.`,
          error: false,
        });
    },
    onError: (error) => {
      setToast({ text: error.message, error: true });
      if (!(error instanceof ApiError)) setAmbiguous(pendingSpec.current);
    },
  });
  function finish(result: CommandResult) {
    setPendingId(null);
    sessionStorage.removeItem(`veloexpress-command-${session.data?.user_id}`);
    refresh();
    if (result.result?.needs_confirmation && pendingSpec.current)
      setWarning({ spec: pendingSpec.current, text: result.result.message });
    else
      setToast({
        text: plain(
          result.result?.message === 'Saved'
            ? 'Изменения сохранены'
            : (result.result?.message ?? 'Готово'),
        ),
        error: result.status !== 'complete',
      });
    if (result.status === 'review') setAmbiguous(null);
  }
  const pending = useQuery({
    queryKey: ['command', pendingId],
    queryFn: () => request<CommandResult>(`/api/commands/${pendingId}`),
    enabled: !!pendingId && !mutation.isPending,
    refetchInterval: (query) =>
      query.state.data && ['complete', 'failed', 'review'].includes(query.state.data.status)
        ? false
        : 2000,
  });
  useEffect(() => {
    if (pending.data && ['complete', 'failed', 'review'].includes(pending.data.status))
      finish(pending.data);
  }, [pending.data]);
  const busy = mutation.isPending || previewBusy || !!pendingId;
  async function act(input: CommandSpec) {
    if (busy) return;
    const spec = {
      ...input,
      request_id: input.request_id ?? crypto.randomUUID(),
    };
    pendingSpec.current = spec;
    if (['cancel_day', 'cancel_lift', 'post', 'extra'].includes(spec.action)) {
      setPreviewBusy(true);
      try {
        setConfirm({ spec, preview: await previewCommand(spec) });
      } catch (error) {
        setToast({
          text: error instanceof Error ? error.message : 'Не удалось открыть подтверждение',
          error: true,
        });
      } finally {
        setPreviewBusy(false);
      }
    } else mutation.mutate({ spec });
  }
  function navigate(next: Tab) {
    setTab(next);
    window.location.hash = next;
    setMobileMenu(false);
  }
  function togglePersonal() {
    const next = !personal;
    setPersonal(next);
    navigate(next ? 'myrides' : 'overview');
  }
  if (session.isPending)
    return (
      <div className="boot-screen">
        <Brand />
        <Loader2 className="spin" size={24} />
        <p>Открываем кабинет…</p>
      </div>
    );
  if (!session.data) return <Login error={session.error} retry={() => session.refetch()} />;
  const title = titles[effectiveTab],
    showPeriod = ['overview', 'analytics', 'riders'].includes(effectiveTab);
  const navItems = isPersonal
    ? [
        { id: 'myrides' as Tab, label: 'Мои поездки', icon: Bike },
        { id: 'analytics' as Tab, label: 'Моя история', icon: BarChart3 },
      ]
    : navigation;
  const mobilePrimary = isPersonal
    ? navItems
    : navigation.filter((item) =>
        ['overview', 'analytics', 'departures', 'planning'].includes(item.id),
      );
  const moreItems = isPersonal
    ? []
    : navigation.filter((item) => ['riders', 'refunds', 'audit'].includes(item.id));
  const moreActive = moreItems.some((item) => item.id === effectiveTab) || mobileMenu;
  return (
    <div className="app-shell">
      <aside id="cabinet-navigation" className={`sidebar ${mobileMenu ? 'sidebar-open' : ''}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="mobile-close"
            onClick={() => setMobileMenu(false)}
            aria-label="Закрыть меню"
          >
            <X size={22} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="live-dot" />
          {isPersonal ? 'Личный кабинет' : 'Кабинет админа'}
        </div>
        <nav aria-label="Разделы кабинета">
          {navItems.map((item) => (
            <button
              className={effectiveTab === item.id ? 'active' : ''}
              key={item.id}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={19} />
              {item.label}
              {effectiveTab === item.id && <span className="nav-marker" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-art">
            <svg viewBox="0 0 180 90" fill="none" aria-hidden="true">
              <path d="M0 75L38 30L64 57L104 8L155 70L180 45" stroke="#627053" strokeWidth="1.5" />
              <path d="M0 89L65 25L110 83L151 32L180 61" stroke="#39482e" strokeWidth="1.5" />
              <circle cx="143" cy="16" r="7" fill="#d7ec79" />
            </svg>
            <span>
              Больше гор.
              <br />
              Больше поездок.
            </span>
          </div>
          <span className="sidebar-location">
            TBILISI, GEORGIA <ArrowUpRight size={13} />
          </span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-identity">
            <Brand compact href={isPersonal ? '#myrides' : '#overview'} />
            <span className="topbar-role">{isPersonal ? 'Райдер' : 'Администрирование'}</span>
          </div>
          <div className="topbar-right">
            <span className={`connection-label ${isDemo ? 'demo-label' : ''}`}>
              <span className="live-dot" />
              {isDemo ? 'Демонстрация' : 'Рабочие данные'}
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={theme === 'light' ? 'Включить тёмную тему' : 'Включить светлую тему'}
              onClick={() => choose(theme === 'light' ? 'dark' : 'light')}
            >
              {theme === 'light' ? <Moon size={19} /> : <Sun size={19} />}
            </Button>
            <Button variant="ghost" size="icon" aria-label="Обновить данные" onClick={refresh}>
              <RefreshCw size={17} />
            </Button>
            <button
              className="profile"
              onClick={session.data.admin ? togglePersonal : undefined}
              title={
                session.data.admin ? 'Переключить кабинет админа / райдера' : session.data.name
              }
            >
              <span className="avatar">{session.data.name.slice(0, 1)}</span>
              <span>
                {session.data.name}
                <small>{isPersonal ? 'Райдер' : 'Администратор'}</small>
              </span>
            </button>
            {!isDemo && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="Выйти"
                onClick={() =>
                  request('/api/auth/logout', { method: 'POST' }).then(() =>
                    window.location.assign('/'),
                  )
                }
              >
                <LogOut size={17} />
              </Button>
            )}
          </div>
        </header>
        <main className={`page-${effectiveTab}`}>
          {isDemo && (
            <div className="demo-banner">
              <CircleHelp size={17} />
              <span className="demo-explanation">
                Демонстрационные данные для review. Действия не меняют базу и не отправляют
                сообщения.
              </span>
              <span className="demo-short">Демо · вымышленные данные</span>
              <button
                onClick={togglePersonal}
                aria-label={isPersonal ? 'Посмотреть админку' : 'Посмотреть кабинет райдера'}
              >
                <span className="demo-switch-long">
                  {isPersonal ? 'Посмотреть админку' : 'Посмотреть кабинет райдера'}
                </span>
                <span className="demo-switch-short">{isPersonal ? 'Админ' : 'Райдер'}</span>
                <ArrowUpRight size={14} />
              </button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <span className="eyebrow">{title.eyebrow}</span>
              <h1>{title.title}</h1>
              <p>{title.subtitle}</p>
            </div>
            {effectiveTab === 'overview' && (
              <div className="season-stamp">
                <Bike size={28} strokeWidth={1.4} />
                <span>
                  RIDE TOGETHER
                  <br />
                  <b>GO FURTHER</b>
                </span>
              </div>
            )}
          </div>
          {effectiveTab === 'overview' && !!days.data?.length && (
            <div className="upcoming-inline">
              <div>
                <span className="live-dot" />
                <b>Ближайший день · {dateLabel(days.data[0].service_date)}</b>
                <span>
                  {days.data[0].booked_rider_count} райдеров · {days.data[0].running_count} выездов
                  набрали минимум
                </span>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setDay(days.data![0].service_date)}
              >
                Открыть день
                <ArrowUpRight size={16} />
              </Button>
            </div>
          )}
          {showPeriod && (
            <div className="period-toolbar">
              <div className="segmented" role="group" aria-label="Период статистики">
                {(['month', 'year', 'all', 'custom'] as const).map((value, i) => (
                  <button
                    aria-pressed={period === value}
                    key={value}
                    onClick={() => {
                      setPeriod(value);
                      setOffset(0);
                      if (value === 'custom' && !custom.start)
                        setCustom(periodRange('month', today));
                    }}
                  >
                    {['Месяц', 'Год', 'Всё время', 'Свой период'][i]}
                  </button>
                ))}
              </div>
              {period === 'custom' ? (
                <div className="date-range">
                  <input
                    type="date"
                    aria-label="Начало периода"
                    value={custom.start}
                    max={custom.end || today}
                    min="2000-01-01"
                    onChange={(e) => setCustom({ ...custom, start: e.target.value })}
                  />
                  <span>—</span>
                  <input
                    type="date"
                    aria-label="Конец периода"
                    value={custom.end}
                    min={custom.start || '2000-01-01'}
                    max={today}
                    onChange={(e) => setCustom({ ...custom, end: e.target.value })}
                  />
                </div>
              ) : period === 'all' ? (
                <span className="period-caption">С первой сохранённой записи</span>
              ) : (
                <div className="period-switch">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Предыдущий период"
                    onClick={() => setOffset(offset - 1)}
                  >
                    <ChevronLeft size={17} />
                  </Button>
                  <b>
                    {period === 'month'
                      ? dateLabel(range.start, {
                          month: 'long',
                          year: 'numeric',
                        })
                      : range.start.slice(0, 4)}
                  </b>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Следующий период"
                    disabled={offset >= 0}
                    onClick={() => setOffset(offset + 1)}
                  >
                    <ChevronRight size={17} />
                  </Button>
                </div>
              )}
            </div>
          )}
          {showPeriod && !validRange && (
            <div className="inline-notice">Выбери корректный период из прошедших дней.</div>
          )}
          {showPeriod && validRange && (
            <Suspense fallback={<div className="skeleton tall" />}>
              <QueryState query={analytics}>
                {analytics.data &&
                  (effectiveTab === 'riders' ? (
                    <RidersTable data={analytics.data} />
                  ) : (
                    <Dashboard
                      data={analytics.data}
                      detailed={effectiveTab === 'analytics'}
                      personal={isPersonal}
                      openDay={(value) => (isPersonal ? setPersonalDay(value) : setDay(value))}
                    />
                  ))}
              </QueryState>
            </Suspense>
          )}
          {effectiveTab === 'departures' && (
            <QueryState query={days}>
              {days.data && <Departures days={days.data} openDay={setDay} />}
            </QueryState>
          )}
          {effectiveTab === 'planning' && (
            <QueryState query={planning}>
              {planning.data && (
                <PlanningView
                  key={planning.data.week_start}
                  data={planning.data}
                  act={act}
                  busy={busy}
                  today={today}
                />
              )}
            </QueryState>
          )}
          {effectiveTab === 'myrides' && (
            <QueryState query={myDays}>
              {myDays.data && <MyRides data={myDays.data} act={act} busy={busy} />}
            </QueryState>
          )}
          {effectiveTab === 'refunds' && (
            <QueryState query={refunds}>
              {refunds.data && <RefundsView reports={refunds.data} />}
            </QueryState>
          )}
          {effectiveTab === 'audit' && (
            <QueryState query={audit}>
              {audit.data && <AuditView entries={audit.data} timezone={session.data.timezone} />}
            </QueryState>
          )}
          <footer className="page-footer">
            <span>VeloExpress · Сделано для наших поездок</span>
            <span>{session.data.timezone}</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div
          className={`toast ${toast.error ? 'toast-error' : ''}`}
          role={toast.error ? 'alert' : 'status'}
        >
          <span>{toast.text}</span>
          <button onClick={() => setToast(null)} aria-label="Закрыть уведомление">
            <X size={17} />
          </button>
        </div>
      )}
      {(busy || ambiguous) && (
        <div className="command-status" role="status">
          {busy ? <Loader2 size={17} className="spin" /> : <CircleHelp size={17} />}
          <span>
            {previewBusy
              ? 'Готовим подтверждение…'
              : mutation.isPending
                ? 'Выполняем запрос…'
                : pendingId
                  ? `Запрос #${pendingId} · ${pending.data?.status === 'running' ? 'выполняется' : 'ожидает бота'}`
                  : 'Связь прервалась: результат запроса пока неизвестен.'}
          </span>
          {ambiguous && !busy && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                const spec = ambiguous;
                setAmbiguous(null);
                mutation.mutate({
                  spec,
                  confirmation: confirm?.preview.confirmation,
                });
              }}
            >
              Проверить тем же запросом
            </Button>
          )}
        </div>
      )}
      {!isPersonal && <DayDialog day={day} onClose={() => setDay(null)} act={act} busy={busy} />}
      <Dialog
        open={!!confirm}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setConfirm(null);
        }}
        title={
          confirm?.spec.action.startsWith('cancel')
            ? 'Подтвердить отмену'
            : 'Подтвердить публикацию'
        }
        description="Проверь последствия перед выполнением действия."
      >
        {confirm && (
          <>
            <pre className="report-text confirmation-report">{plain(confirm.preview.details)}</pre>
            {confirm.preview.affected !== undefined && (
              <p className="caption">Участников в дне: {confirm.preview.affected}</p>
            )}
            <div className="dialog-actions">
              <Button
                variant="secondary"
                disabled={mutation.isPending}
                onClick={() => setConfirm(null)}
              >
                Вернуться
              </Button>
              <Button
                variant={confirm.spec.action.startsWith('cancel') ? 'destructive' : 'default'}
                disabled={mutation.isPending}
                onClick={() => {
                  pendingSpec.current = confirm.spec;
                  mutation.mutate({
                    spec: confirm.spec,
                    confirmation: confirm.preview.confirmation,
                  });
                }}
              >
                {mutation.isPending ? 'Выполняется…' : 'Подтвердить'}
              </Button>
            </div>
          </>
        )}
      </Dialog>
      <Dialog
        open={!!warning}
        onOpenChange={(open) => {
          if (!open) setWarning(null);
        }}
        title="Проверь оплату"
        description="Сумма зависит от текущих бронирований."
      >
        {warning && (
          <>
            <pre className="report-text">{plain(warning.text)}</pre>
            <div className="dialog-actions">
              <Button variant="secondary" onClick={() => setWarning(null)}>
                Вернуться
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  const spec = {
                    ...warning.spec,
                    request_id: crypto.randomUUID(),
                    acknowledged: true,
                  };
                  setWarning(null);
                  act(spec);
                }}
              >
                Всё верно, отметить
              </Button>
            </div>
          </>
        )}
      </Dialog>
      <Dialog
        open={!!personalDay}
        onOpenChange={(open) => {
          if (!open) setPersonalDay(null);
        }}
        title={personalDay ? `Мои поездки · ${dateLabel(personalDay)}` : 'Мои поездки'}
        description="Только твои данные за выбранный день."
      >
        {personalDay &&
          (() => {
            const row = analytics.data?.days.find((d) => d.date === personalDay);
            return row ? (
              <div className="detail-metrics">
                <span>
                  Выездов<strong>{row.lifts}</strong>
                </span>
                <span>
                  Мест<strong>{row.seats}</strong>
                </span>
                <span>
                  Отмечено<strong>{money(row.net_gel)}</strong>
                </span>
              </div>
            ) : (
              <p>В этот день у тебя нет сохранённых поездок.</p>
            );
          })()}
      </Dialog>
      <nav
        className={`bottom-nav ${isPersonal ? 'bottom-nav-personal' : ''}`}
        aria-label="Основная навигация"
      >
        {mobilePrimary.map((item) => (
          <button
            key={item.id}
            className={effectiveTab === item.id ? 'active' : ''}
            aria-current={effectiveTab === item.id ? 'page' : undefined}
            onClick={() => navigate(item.id)}
          >
            <span className="bottom-nav-icon">
              <item.icon size={22} strokeWidth={1.8} />
            </span>
            <span>{item.id === 'planning' ? 'План' : item.label}</span>
          </button>
        ))}
        <button
          className={moreActive ? 'active' : ''}
          onClick={() => setMobileMenu(true)}
          aria-expanded={mobileMenu}
          aria-label="Ещё разделы и настройки"
        >
          <span className="bottom-nav-icon">
            <MoreHorizontal size={23} />
          </span>
          <span>Ещё</span>
        </button>
      </nav>
      <Dialog
        open={mobileMenu}
        onOpenChange={setMobileMenu}
        sheet
        title="Твой кабинет"
        description={isPersonal ? 'Настройки и оформление.' : 'Участники, отчёты и настройки.'}
      >
        <div className="more-links">
          {moreItems.map((item) => (
            <button
              key={item.id}
              className={effectiveTab === item.id ? 'active' : ''}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={21} />
              <span>{item.label}</span>
              <ChevronRight size={17} />
            </button>
          ))}
        </div>
        <div className="theme-settings">
          <span className="eyebrow">ОФОРМЛЕНИЕ</span>
          <div className="theme-options" role="group" aria-label="Тема оформления">
            {(
              [
                { value: 'light', label: 'Светлая', icon: Sun },
                { value: 'dark', label: 'Тёмная', icon: Moon },
                { value: 'system', label: 'Авто', icon: Monitor },
              ] as const
            ).map((option) => (
              <button
                key={option.value}
                aria-pressed={preference === option.value}
                onClick={() => choose(option.value)}
              >
                <option.icon size={20} />
                <span>{option.label}</span>
              </button>
            ))}
          </div>
          <p className="caption">
            В режиме «Авто» тема следует настройкам Telegram или твоего устройства.
          </p>
        </div>
        {session.data.admin && (
          <Button
            variant="secondary"
            className="more-account"
            onClick={() => {
              togglePersonal();
              setMobileMenu(false);
            }}
          >
            <Users size={18} />
            {isPersonal ? 'Перейти в кабинет админа' : 'Посмотреть мои поездки'}
          </Button>
        )}
        {!isDemo && (
          <Button
            variant="ghost"
            className="more-account"
            onClick={() =>
              request('/api/auth/logout', { method: 'POST' }).then(() =>
                window.location.assign('/'),
              )
            }
          >
            <LogOut size={18} />
            Выйти из кабинета
          </Button>
        )}
      </Dialog>
    </div>
  );
}
function Login({ error, retry }: { error: Error | null; retry(): void }) {
  const config = useQuery({
    queryKey: ['config'],
    queryFn: () => request<{ browser_login: boolean }>('/api/config'),
    retry: false,
  });
  return (
    <div className="login-page">
      <div className="login-brand">
        <Brand />
      </div>
      <section className="login-card">
        <span className="eyebrow">TBILISI · MOUNTAINS · COMMUNITY</span>
        <h1>
          Меньше хлопот.
          <br />
          <span>Больше поездок.</span>
        </h1>
        <p>Твои выезды и оплаты. Для админов — вся картина сезона и управление расписанием.</p>
        <Button asChild disabled={!config.data?.browser_login}>
          <a href={config.data?.browser_login ? '/api/auth/login' : undefined}>
            Войти через Telegram
            <ArrowUpRight size={18} />
          </a>
        </Button>
        {!config.data?.browser_login && (
          <p className="caption">
            Открой Mini App из Telegram-бота. Вход из браузера станет доступен после настройки
            Telegram Login.
          </p>
        )}
        <a className="demo-login-link" href="/?demo=1">
          Посмотреть демонстрацию
          <ArrowUpRight size={15} />
        </a>
        {error && !(error instanceof ApiError && error.status === 401) && (
          <div className="error-state">
            <p>Не удалось подключиться к кабинету.</p>
            <Button variant="ghost" onClick={retry}>
              Повторить
            </Button>
          </div>
        )}
      </section>
      <div className="login-art" aria-hidden="true">
        <svg viewBox="0 0 500 400">
          <path d="M0 340L100 160L180 260L310 40L500 340" fill="#dde7be" />
          <path d="M0 400L180 180L290 310L400 190L500 360V400Z" fill="#17291c" />
          <path
            d="M70 360C150 350 150 300 205 290S280 270 305 230"
            fill="none"
            stroke="#c8df69"
            strokeWidth="3"
            strokeDasharray="6 8"
          />
          <circle cx="402" cy="67" r="27" fill="#c8df69" />
        </svg>
        <span>RIDE TOGETHER. GO FURTHER.</span>
      </div>
    </div>
  );
}
function QueryState({
  query,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: Error | null;
    refetch(): unknown;
  };
  children: React.ReactNode;
}) {
  if (query.isPending)
    return (
      <div className="loading-grid" aria-label="Загрузка данных">
        <div className="skeleton" />
        <div className="skeleton" />
        <div className="skeleton tall" />
      </div>
    );
  if (query.isError)
    return (
      <div className="panel error-state">
        <p>{query.error?.message ?? 'Не удалось загрузить данные.'}</p>
        <Button variant="secondary" onClick={() => query.refetch()}>
          Повторить
        </Button>
      </div>
    );
  return <>{children}</>;
}
