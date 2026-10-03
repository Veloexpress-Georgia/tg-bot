import { Bike, ChevronLeft, ChevronRight, Download, Gauge, Users, Wallet } from 'lucide-react';
import { useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useAnalytics, useLiveDays } from '../../app/queries';
import { navigate } from '../../app/router';
import { t } from '../../i18n';
import { capitalize, chartDate, cx, dateLabel, money, num, periodRange } from '../../lib';
import type { Analytics, Session } from '../../types';
import {
  Button,
  Card,
  Empty,
  List,
  Meter,
  Notice,
  Page,
  PageHeader,
  Query,
  Row,
  Section,
  Segmented,
  Stat,
  StatGrid,
  useMedia,
} from '../../ui';
import { DemandPanel } from './DemandPanel';
import { delta, periodRequest, usePeriod, type PeriodState } from './period';
import './analytics.css';

type Part = 'trend' | 'load' | 'money' | 'people' | 'days';

export function AnalyticsPage({
  session,
  personal = false,
}: {
  session: Session;
  personal?: boolean;
}) {
  const [period, setPeriod] = usePeriod();
  const request = periodRequest(period, session.today, personal);
  const analytics = useAnalytics(request.path, request.valid);
  return (
    <Page wide>
      <PageHeader
        title={personal ? t.analytics.personalTitle : t.analytics.title}
        subtitle={personal ? t.analytics.personalSubtitle : t.analytics.subtitle}
      />
      <PeriodToolbar
        value={period}
        onChange={setPeriod}
        range={request.range}
        today={session.today}
      />
      {!request.valid ? (
        <Notice tone="warn">{t.analytics.invalidRange}</Notice>
      ) : (
        <Query query={analytics}>{(data) => <Dashboard data={data} personal={personal} />}</Query>
      )}
    </Page>
  );
}

export function PeriodToolbar({
  value,
  onChange,
  range,
  today,
}: {
  value: PeriodState;
  onChange(next: Partial<PeriodState>): void;
  range: { start: string; end: string };
  today: string;
}) {
  return (
    <div className="period">
      <Segmented
        label={t.analytics.period}
        value={value.period}
        onChange={(period) =>
          onChange({
            period,
            offset: 0,
            ...(period === 'custom' && !value.custom.start
              ? { custom: periodRange('month', today) }
              : {}),
          })
        }
        options={[
          { value: 'month', label: t.analytics.month },
          { value: 'year', label: t.analytics.year },
          { value: 'all', label: t.analytics.all },
          { value: 'custom', label: t.analytics.custom },
        ]}
      />
      {value.period === 'custom' ? (
        <div className="period-dates">
          <input
            type="date"
            aria-label={t.analytics.from}
            value={value.custom.start}
            min="2000-01-01"
            max={value.custom.end || today}
            onChange={(event) =>
              onChange({ custom: { ...value.custom, start: event.target.value } })
            }
          />
          <span aria-hidden="true">—</span>
          <input
            type="date"
            aria-label={t.analytics.to}
            value={value.custom.end}
            min={value.custom.start || '2000-01-01'}
            max={today}
            onChange={(event) => onChange({ custom: { ...value.custom, end: event.target.value } })}
          />
        </div>
      ) : value.period === 'all' ? (
        <p className="caption period-caption">{t.analytics.sinceFirst}</p>
      ) : (
        <div className="period-step">
          <Button
            variant="ghost"
            size="icon"
            aria-label={t.analytics.previous}
            onClick={() => onChange({ offset: value.offset - 1 })}
          >
            <ChevronLeft size={18} />
          </Button>
          <b>
            {value.period === 'month'
              ? capitalize(dateLabel(range.start, { month: 'long', year: 'numeric' }))
              : range.start.slice(0, 4)}
          </b>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t.analytics.next}
            disabled={value.offset >= 0}
            onClick={() => onChange({ offset: value.offset + 1 })}
          >
            <ChevronRight size={18} />
          </Button>
        </div>
      )}
    </div>
  );
}

function Dashboard({ data, personal }: { data: Analytics; personal: boolean }) {
  const wide = useMedia('(min-width: 900px)');
  const [part, setPart] = useState<Part>('trend');
  // Phones mount one part at a time, so hidden charts never measure a zero width.
  const show = (value: Part) => wide || part === value;
  const summary = data.summary;
  const openDay = (date: string) => navigate({ name: 'day', date, tab: 'lifts' });
  return (
    <>
      <StatGrid>
        <Stat
          accent
          label={t.analytics.reported}
          icon={<Wallet size={18} />}
          value={money(summary.net_gel)}
          note={delta(summary.net_gel, data.previous.net_gel)}
        />
        <Stat
          label={t.analytics.seats}
          icon={<Bike size={18} />}
          value={num(summary.seats)}
          note={t.analytics.liftsAndDays(summary.lifts, summary.days)}
        />
        <Stat
          label={personal ? t.analytics.rideDays : t.analytics.riders}
          icon={<Users size={18} />}
          value={num(personal ? summary.days : summary.riders)}
          note={
            personal ? t.analytics.savedHistory : t.analytics.newRiders(summary.new_riders ?? 0)
          }
        />
        <Stat
          label={personal ? t.analytics.guestSeats : t.analytics.occupancy}
          icon={<Gauge size={18} />}
          value={personal ? num(summary.guests) : `${num(summary.occupancy_pct)}%`}
          note={
            personal
              ? t.analytics.guestNote
              : t.analytics.seatsOfCapacity(summary.seats, summary.capacity)
          }
        />
      </StatGrid>
      {!wide && (
        <Segmented
          chips
          className="analytics-tabs"
          label={t.analytics.sections}
          value={part}
          onChange={setPart}
          options={[
            { value: 'trend', label: t.analytics.parts.trend },
            { value: 'load', label: personal ? t.analytics.parts.lifts : t.analytics.parts.demand },
            { value: 'money', label: t.analytics.parts.money },
            { value: 'people', label: t.analytics.parts.people },
            { value: 'days', label: t.analytics.parts.days },
          ]}
        />
      )}
      <div className="analytics-grid">
        {show('trend') && <TrendCard data={data} openDay={openDay} />}
        {show('load') &&
          (!personal && data.demand ? (
            <DemandPanelLive data={data} />
          ) : (
            <LoadCard data={data} personal={personal} />
          ))}
        {show('money') && <MoneyCard data={data} />}
        {show('people') && <PeopleCard data={data} />}
        {show('days') && <DaysCard data={data} openDay={openDay} />}
      </div>
      <p className="caption analytics-note">
        {t.analytics.historyFrom(
          data.first_record
            ? dateLabel(data.first_record, { day: 'numeric', month: 'long', year: 'numeric' })
            : null,
        )}{' '}
        {summary.backfilled_days > 0 && t.analytics.backfilled(summary.backfilled_days)}{' '}
        {t.analytics.moneyNote}
      </p>
    </>
  );
}

function DemandPanelLive({ data }: { data: Analytics }) {
  const live = useLiveDays();
  return (
    <DemandPanel
      data={data.demand!}
      liveDays={live.data ?? []}
      liveStatus={live.isError ? 'error' : live.data ? 'ready' : 'pending'}
      openDay={(date) => navigate({ name: 'day', date, tab: 'lifts' })}
    />
  );
}

const tooltipStyle = {
  borderRadius: 12,
  border: '1px solid var(--line)',
  background: 'var(--surface)',
  color: 'var(--ink)',
};

function TrendCard({ data, openDay }: { data: Analytics; openDay(date: string): void }) {
  const [chart, setChart] = useState<'money' | 'seats'>('money');
  const summary = data.summary;
  const moneyChart = chart === 'money';
  return (
    <Card className="analytics-card analytics-trend">
      <div className="card-head">
        <h2>{moneyChart ? t.analytics.moneyTrend : t.analytics.seatsTrend}</h2>
        <Segmented
          size="sm"
          label={t.analytics.chart}
          value={chart}
          onChange={setChart}
          options={[
            { value: 'money', label: t.analytics.payments },
            { value: 'seats', label: t.analytics.seatsShort },
          ]}
        />
      </div>
      <p className="trend-total">
        <strong className="tabular">
          {moneyChart ? money(summary.net_gel) : num(summary.seats)}
        </strong>
        <span>
          {delta(
            moneyChart ? summary.net_gel : summary.seats,
            moneyChart ? data.previous.net_gel : data.previous.seats,
          )}
        </span>
      </p>
      <div
        className="chart"
        role="img"
        aria-label={moneyChart ? t.analytics.moneyChartLabel : t.analytics.seatsChartLabel}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data.series}
            margin={{ top: 10, right: 6, left: -18, bottom: 0 }}
            onClick={(state) => {
              if (data.granularity === 'day' && typeof state.activeLabel === 'string')
                openDay(state.activeLabel);
            }}
          >
            <defs>
              <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--chart-fill)" stopOpacity={0.5} />
                <stop offset="100%" stopColor="var(--chart-fill)" stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 5" />
            <XAxis
              dataKey="date"
              axisLine={false}
              tickLine={false}
              minTickGap={36}
              tick={{ fill: 'var(--ink-2)', fontSize: 12 }}
              tickFormatter={(value) => chartDate(value, data.granularity)}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fill: 'var(--ink-2)', fontSize: 12 }}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(value) => chartDate(String(value), data.granularity)}
              formatter={(value, name) => [
                moneyChart ? money(Number(value)) : num(Number(value)),
                name,
              ]}
            />
            <Area
              isAnimationActive={false}
              type="monotone"
              dataKey={moneyChart ? 'expected_gel' : 'capacity'}
              name={moneyChart ? t.analytics.expected : t.analytics.capacity}
              stroke="var(--chart-dash)"
              fill="transparent"
              strokeDasharray="4 5"
              strokeWidth={1.5}
            />
            <Area
              isAnimationActive={false}
              type="monotone"
              dataKey={moneyChart ? 'net_gel' : 'seats'}
              name={moneyChart ? t.analytics.reported : t.analytics.seatsTaken}
              stroke="var(--chart-stroke)"
              fill="url(#trend-fill)"
              strokeWidth={2.5}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-legend">
        <span>
          <i className="legend-dot" />
          {moneyChart ? t.analytics.reported : t.analytics.seatsTaken}
        </span>
        <span>
          <i className="legend-dash" />
          {moneyChart ? t.analytics.expected : t.analytics.capacity}
        </span>
      </div>
    </Card>
  );
}

function LoadCard({ data, personal }: { data: Analytics; personal: boolean }) {
  return (
    <Card className="analytics-card">
      <div className="card-head">
        <h2>{personal ? t.analytics.yourLifts : t.analytics.byTime}</h2>
      </div>
      {data.by_time.length ? (
        <div className="load-list">
          {data.by_time.map((row) => (
            <div className="load-row" key={row.time}>
              <span className="load-time tabular">{row.time}</span>
              <span className="load-main">
                <Meter value={row.occupancy_pct} max={100} />
                <small>{t.analytics.seatsAndLifts(row.seats, row.lifts)}</small>
              </span>
              <b className="tabular">{num(row.occupancy_pct)}%</b>
            </div>
          ))}
        </div>
      ) : (
        <Empty title={t.analytics.noLifts} />
      )}
      <p className="caption card-note">{t.analytics.loadNote}</p>
    </Card>
  );
}

function MoneyCard({ data }: { data: Analytics }) {
  const summary = data.summary;
  const methods = [
    {
      name: t.analytics.transfers,
      value: Math.max(summary.transfer_gel, 0),
      color: 'var(--chart-fill)',
    },
    { name: t.analytics.cash, value: Math.max(summary.cash_gel, 0), color: 'var(--chart-cash)' },
    {
      name: t.analytics.unknownMethod,
      value: Math.max(summary.unknown_gel, 0),
      color: 'var(--chart-unknown)',
    },
  ].filter((method) => method.value);
  return (
    <Card className="analytics-card">
      <div className="card-head">
        <h2>{t.analytics.moneyTitle}</h2>
      </div>
      <div className="money-split">
        <div className="donut" role="img" aria-label={t.analytics.methodsLabel}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                isAnimationActive={false}
                data={methods}
                dataKey="value"
                innerRadius="66%"
                outerRadius="90%"
                paddingAngle={3}
                stroke="none"
              >
                {methods.map((method) => (
                  <Cell key={method.name} fill={method.color} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value) => money(Number(value))}
                contentStyle={tooltipStyle}
                itemStyle={{ color: 'var(--ink)' }}
              />
            </PieChart>
          </ResponsiveContainer>
          <span>
            <b className="tabular">{money(summary.net_gel)}</b>
            {t.analytics.reportedShort}
          </span>
        </div>
        <ul className="money-methods">
          {methods.length ? (
            methods.map((method) => (
              <li key={method.name}>
                <span>
                  <i className="legend-dot" style={{ background: method.color }} />
                  {method.name}
                </span>
                <b className="tabular">{money(method.value)}</b>
              </li>
            ))
          ) : (
            <li className="caption">{t.analytics.noPayments}</li>
          )}
        </ul>
      </div>
      <dl className="lines">
        <div>
          <dt>{t.analytics.added}</dt>
          <dd className="tabular">{money(summary.received_gel)}</dd>
        </div>
        <div>
          <dt>{t.analytics.reversed}</dt>
          <dd className="tabular">{money(summary.reversed_gel)}</dd>
        </div>
        <div>
          <dt>{t.analytics.expected}</dt>
          <dd className="tabular">{money(summary.expected_gel)}</dd>
        </div>
      </dl>
    </Card>
  );
}

function PeopleCard({ data }: { data: Analytics }) {
  const summary = data.summary;
  return (
    <Card className="analytics-card">
      <div className="card-head">
        <h2>{t.analytics.peopleTitle}</h2>
      </div>
      <p className="people-number tabular">
        {num(summary.seats)}
        <span>{t.analytics.seatsTakenLong}</span>
      </p>
      <dl className="lines">
        <div>
          <dt>{t.analytics.guestSeats}</dt>
          <dd className="tabular">{num(summary.guests)}</dd>
        </div>
        <div>
          <dt>{t.analytics.offlineSeats}</dt>
          <dd className="tabular">{num(summary.manual)}</dd>
        </div>
        <div>
          <dt>{t.analytics.cancelledLifts}</dt>
          <dd className="tabular">{num(summary.cancelled_lifts)}</dd>
        </div>
      </dl>
      <p className="caption card-note">{t.analytics.peopleNote}</p>
    </Card>
  );
}

function DaysCard({ data, openDay }: { data: Analytics; openDay(date: string): void }) {
  const [limit, setLimit] = useState(10);
  function exportCsv() {
    const rows = [
      t.analytics.csvHeader,
      ...data.days.map((day) => [
        day.date,
        day.lifts,
        day.seats,
        day.occupancy_pct,
        day.net_gel,
        day.backfilled_days,
      ]),
    ];
    const blob = new Blob(['﻿' + rows.map((row) => row.join(';')).join('\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `veloexpress-${data.start}-${data.end}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <Section
      className="analytics-days"
      title={t.analytics.daysTitle}
      action={
        <Button variant="ghost" size="sm" disabled={!data.days.length} onClick={exportCsv}>
          <Download size={16} />
          CSV
        </Button>
      }
    >
      {data.days.length ? (
        <Card flush>
          <List>
            {data.days.slice(0, limit).map((day) => (
              <Row
                key={day.date}
                title={
                  <>
                    {capitalize(
                      dateLabel(day.date, {
                        weekday: 'short',
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      }),
                    )}
                    {day.backfilled_days > 0 && (
                      <span className="badge badge-muted day-flag">{t.analytics.restored}</span>
                    )}
                  </>
                }
                subtitle={t.analytics.dayLine(
                  day.lifts,
                  day.seats,
                  day.capacity,
                  day.occupancy_pct,
                )}
                trailing={<span className="tabular">{money(day.net_gel)}</span>}
                onClick={() => openDay(day.date)}
              />
            ))}
          </List>
        </Card>
      ) : (
        <Card>
          <Empty title={t.analytics.noHistory} />
        </Card>
      )}
      {data.days.length > limit && (
        <Button
          variant="ghost"
          block
          className={cx('show-more')}
          onClick={() => setLimit(limit + 20)}
        >
          {t.analytics.showMore(Math.min(20, data.days.length - limit))}
        </Button>
      )}
    </Section>
  );
}
