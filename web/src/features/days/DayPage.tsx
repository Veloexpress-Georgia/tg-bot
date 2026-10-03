import { Ban, ChevronDown, Hourglass, Wallet } from 'lucide-react';
import { Fragment } from 'react';
import { useCommands } from '../../app/commands';
import { useDay } from '../../app/queries';
import { navigate, type DayTab, type Route } from '../../app/router';
import { t } from '../../i18n';
import { cx, money } from '../../lib';
import type { HistoricalDay as HistoricalDayData, LiveDay, Session } from '../../types';
import {
  Badge,
  Card,
  Empty,
  List,
  Notice,
  Page,
  PageHeader,
  Query,
  Row,
  Section,
  Segmented,
  Skeleton,
  Stat,
  StatGrid,
} from '../../ui';
import { AuditList } from '../reports/ReportPages';
import { AttentionList, LiftBoard, MoneyCard, daySubtitle, useClock } from './DaySummary';
import { dayAttention, longDate, nextLiftTime, paysCash, relativeDay } from './model';
import { PaymentMark } from './PaymentMark';
import './days.css';

type PaymentFilter = 'all' | 'unpaid' | 'cash' | 'marked';

export function DayPage({
  route,
  session,
}: {
  route: Extract<Route, { name: 'day' }>;
  session: Session;
}) {
  const detail = useDay(route.date);
  return (
    <Page>
      <Query query={detail}>
        {(day) =>
          day.historical ? (
            <HistoricalDay day={day} />
          ) : (
            <LiveDayView day={day} route={route} session={session} />
          )
        }
      </Query>
    </Page>
  );
}

function LiveDayView({
  day,
  route,
  session,
}: {
  day: LiveDay;
  route: Extract<Route, { name: 'day' }>;
  session: Session;
}) {
  const { act, busy } = useCommands();
  const attention = dayAttention(day);
  const minutes = useClock(session.timezone);
  const date = day.service_date;
  const show = (tab: DayTab, filter?: string) =>
    navigate({ name: 'day', date, tab, ...(filter ? { filter } : {}) }, { replace: true });
  return (
    <>
      <PageHeader
        eyebrow={relativeDay(date, session.today)}
        title={longDate(date)}
        subtitle={daySubtitle(day)}
      />
      <Segmented
        className="day-tabs"
        label={t.day.sections}
        value={route.tab}
        onChange={(tab) => show(tab)}
        options={[
          { value: 'lifts', label: t.day.tabs.lifts },
          {
            value: 'payments',
            label: t.day.tabs.payments,
            count: attention.unpaid.length || undefined,
          },
          {
            value: 'requests',
            label: t.day.tabs.requests,
            count: attention.commands.length || undefined,
          },
        ]}
      />
      {route.tab === 'lifts' && (
        <>
          <Section title={t.home.lifts}>
            <LiftBoard
              day={day}
              next={nextLiftTime(day, session.today, minutes)}
              onOpen={(time) => navigate({ name: 'lift', date, time })}
            />
          </Section>
          <Section title={t.home.payments}>
            <MoneyCard
              day={day}
              attention={attention}
              onOpen={() => show('payments', attention.unpaid.length ? 'unpaid' : undefined)}
            />
          </Section>
          <AttentionList
            attention={attention}
            onRequests={() => show('requests')}
            onLift={(time) => navigate({ name: 'lift', date, time })}
          />
          {!day.past && (
            <Section title={t.day.manage}>
              <Card flush>
                <List>
                  <Row
                    leading={<Ban size={19} />}
                    tone="danger"
                    title={t.day.cancelDay}
                    subtitle={t.day.cancelDayHint}
                    disabled={busy}
                    onClick={() => act({ action: 'cancel_day', service_date: date })}
                  />
                </List>
              </Card>
            </Section>
          )}
        </>
      )}
      {route.tab === 'payments' && (
        <PaymentsTab
          day={day}
          filter={(route.filter as PaymentFilter | undefined) ?? 'all'}
          onFilter={(filter) => show('payments', filter === 'all' ? undefined : filter)}
        />
      )}
      {route.tab === 'requests' &&
        (day.commands ? (
          <Section className="stack">
            <Notice>{t.day.requestsNotice}</Notice>
            <AuditList entries={day.commands} timezone={session.timezone} />
          </Section>
        ) : (
          <Skeleton height={160} />
        ))}
    </>
  );
}

type DayRider = NonNullable<LiveDay['riders']>[number];

const paymentRank = (rider: DayRider) =>
  rider.due_now_gel > rider.paid_gel ? 0 : rider.due_now_gel ? (paysCash(rider) ? 1 : 2) : 3;

function PaymentsTab({
  day,
  filter,
  onFilter,
}: {
  day: LiveDay;
  filter: PaymentFilter;
  onFilter(filter: PaymentFilter): void;
}) {
  const attention = dayAttention(day);
  if (!day.riders)
    return (
      <Section>
        <Skeleton height={240} />
      </Section>
    );
  const riders = [...day.riders].sort(
    (a, b) => paymentRank(a) - paymentRank(b) || a.label.localeCompare(b.label),
  );
  const marked = riders.filter((r) => r.due_now_gel > 0 && r.paid_gel >= r.due_now_gel);
  const shown =
    filter === 'unpaid'
      ? attention.unpaid
      : filter === 'cash'
        ? attention.cash
        : filter === 'marked'
          ? marked
          : riders;
  return (
    <>
      <Section>
        <MoneyCard day={day} attention={attention} />
      </Section>
      <Section>
        <Segmented
          chips
          label={t.payments.filter}
          value={filter}
          onChange={onFilter}
          options={[
            { value: 'all', label: t.payments.all, count: riders.length },
            { value: 'unpaid', label: t.payments.unpaid, count: attention.unpaid.length },
            { value: 'cash', label: t.payments.cash, count: attention.cash.length },
            { value: 'marked', label: t.payments.marked, count: marked.length },
          ]}
        />
      </Section>
      <Section>
        {shown.length ? (
          <Card flush>
            <List>
              {shown.map((rider) => (
                <Row
                  key={rider.user_id}
                  title={rider.label}
                  subtitle={riderLifts(rider)}
                  trailing={<RiderBalance rider={rider} />}
                />
              ))}
            </List>
          </Card>
        ) : (
          <Card>
            <Empty icon={<Wallet size={24} />} title={t.payments.emptyFilter} />
          </Card>
        )}
        <p className="caption section-note">{t.payments.disclaimer}</p>
      </Section>
    </>
  );
}

function riderLifts(rider: DayRider) {
  return rider.rows.map((row, index) => (
    <Fragment key={row.lift_time}>
      {index > 0 && ', '}
      {row.lift_time}
      {row.waitlist_position > 0 && (
        <span className="lift-wait">
          <Hourglass size={12} />
          {row.waitlist_position}
        </span>
      )}
    </Fragment>
  ));
}

function RiderBalance({ rider }: { rider: DayRider }) {
  const missing = rider.due_now_gel - rider.paid_gel;
  if (missing > 0) return <PaymentMark state="due" amount={missing} />;
  if (rider.due_now_gel === 0) return <PaymentMark state="none" />;
  return <PaymentMark state={paysCash(rider) ? 'cash' : 'paid'} />;
}

function HistoricalDay({ day }: { day: HistoricalDayData }) {
  return (
    <>
      <PageHeader
        eyebrow={day.cancelled ? t.history.cancelledDay : t.history.finishedDay}
        title={longDate(day.service_date, true)}
        subtitle={day.reconstructed ? t.history.reconstructed : t.history.saved}
      />
      <StatGrid columns={3}>
        <Stat label={t.history.received} value={money(day.received_gel)} />
        <Stat label={t.history.reversed} value={money(day.refunded_gel)} />
        <Stat label={t.history.price} value={money(day.price_gel)} />
      </StatGrid>
      <Section title={t.home.lifts}>
        {day.lifts.length ? (
          <Card flush>
            <ul className="list">
              {day.lifts.map((lift) => (
                <li className="row-item" key={lift.lift_time}>
                  <details className="disclosure">
                    <summary className="row">
                      <span className="lift-time tabular">{lift.lift_time}</span>
                      <span className="row-main">
                        <span className="row-title">
                          {t.history.seats(lift.seats, lift.capacity)}
                        </span>
                        <span className="row-sub">
                          {lift.waiting_count === null
                            ? t.history.queueUnknown
                            : t.history.queue(lift.waiting_count)}
                        </span>
                      </span>
                      <Badge tone={lift.ran ? 'ok' : 'muted'}>
                        {lift.ran ? t.history.ran : t.history.notRan}
                      </Badge>
                      <ChevronDown className="disclosure-icon" size={18} />
                    </summary>
                    <ul className="disclosure-body">
                      {lift.riders.map((rider, index) => (
                        <li key={index}>
                          <span>{rider.label || t.history.offline}</span>
                          <span className={cx('tone-muted', 'tabular')}>
                            {t.history.riderSeats(rider.seats, rider.guests)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                </li>
              ))}
            </ul>
          </Card>
        ) : (
          <Card>
            <Empty title={t.history.noLifts} />
          </Card>
        )}
      </Section>
    </>
  );
}
