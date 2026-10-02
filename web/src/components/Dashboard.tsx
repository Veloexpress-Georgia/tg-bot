import { Metric, Empty } from './Shared';
import { useState } from 'react';
import { useCompactLayout } from '../layout';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChevronRight,
  Download,
  Users,
  Bike,
  Wallet,
  Gauge,
} from 'lucide-react';
import type { Analytics, LiveDay } from '../types';
import { DemandPanel } from './DemandPanel';
import { chartDate, dateLabel, delta, money, number } from '../lib';
import { Button } from './ui/button';

export function Dashboard({
  data,
  openDay,
  detailed = false,
  personal = false,
  liveDays = [],
  liveStatus = 'ready',
}: {
  data: Analytics;
  openDay(day: string): void;
  detailed?: boolean;
  personal?: boolean;
  liveDays?: LiveDay[];
  liveStatus?: 'pending' | 'error' | 'ready';
}) {
  const [section, setSection] = useState('trend');
  const [chart, setChart] = useState<'money' | 'seats'>('money');
  const compact = useCompactLayout();
  const showTrend = !compact || !detailed || section === 'trend';
  const showLoad = !compact || (detailed && section === 'load');
  const showMoney = detailed && (!compact || section === 'money');
  const showPeople = detailed && (!compact || section === 'people');
  const showHistory = !compact || (detailed && section === 'days');
  const summary = data.summary,
    previous = data.previous;
  const methods = [
    {
      name: 'Переводы',
      value: Math.max(summary.transfer_gel, 0),
      color: '#b9d552',
    },
    {
      name: 'Наличные',
      value: Math.max(summary.cash_gel, 0),
      color: 'var(--chart-cash)',
    },
    {
      name: 'Способ не указан',
      value: Math.max(summary.unknown_gel, 0),
      color: 'var(--chart-unknown)',
    },
  ].filter((m) => m.value);
  return (
    <div
      className={`dashboard-workspace ${detailed ? 'dashboard-detailed' : 'dashboard-overview'}`}
      data-analysis-section={section}
    >
      {detailed && (
        <div className="analysis-tabs" role="group" aria-label="Раздел аналитики">
          {[
            ['trend', 'График'],
            ['load', personal ? 'Выезды' : 'Спрос'],
            ['money', 'Деньги'],
            ['people', 'Люди'],
            ['days', 'Дни'],
          ].map(([key, label]) => (
            <button key={key} aria-pressed={section === key} onClick={() => setSection(key)}>
              {label}
            </button>
          ))}
        </div>
      )}
      {(!compact || !detailed || section === 'trend') && (
        <div className="metrics-grid">
          <Metric
            title="Отмечено оплат"
            value={money(summary.net_gel)}
            note={delta(summary.net_gel, previous.net_gel)}
            icon={Wallet}
            accent
          />
          <Metric
            title="Мест на выездах"
            value={number(summary.seats)}
            note={`${number(summary.lifts)} выездов · ${number(summary.days)} дней`}
            icon={Bike}
          />
          <Metric
            title={personal ? 'Дни поездок' : 'Уникальных райдеров'}
            value={number(personal ? summary.days : summary.riders)}
            note={
              personal
                ? 'По сохранённой истории'
                : `${number(summary.new_riders ?? 0)} впервые в сохранённой истории`
            }
            icon={Users}
          />
          <Metric
            title={personal ? 'Гостевых мест' : 'Загрузка'}
            value={personal ? number(summary.guests) : `${number(summary.occupancy_pct)}%`}
            note={
              personal
                ? 'В твоих сохранённых поездках'
                : `${number(summary.seats)} из ${number(summary.capacity)} мест`
            }
            icon={Gauge}
          />
        </div>
      )}
      {(showTrend || showLoad) && (
        <div className="dashboard-charts">
          {showTrend && (
            <section className="panel chart-main">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">ДИНАМИКА ПЕРИОДА</span>
                  <h2>{chart === 'money' ? 'Деньги в движении' : 'Как заполняются выезды'}</h2>
                </div>
                <div className="segmented compact">
                  <button aria-pressed={chart === 'money'} onClick={() => setChart('money')}>
                    Оплаты
                  </button>
                  <button aria-pressed={chart === 'seats'} onClick={() => setChart('seats')}>
                    Места
                  </button>
                </div>
              </div>
              <div className="chart-total">
                <strong>
                  {chart === 'money' ? money(summary.net_gel) : number(summary.seats)}
                </strong>
                <span>
                  <ArrowUpRight size={16} />{' '}
                  {delta(
                    chart === 'money' ? summary.net_gel : summary.seats,
                    chart === 'money' ? previous.net_gel : previous.seats,
                  )}
                </span>
              </div>
              <div
                className="chart-frame"
                role="img"
                aria-label={
                  chart === 'money'
                    ? 'График отмеченных оплат и ожидаемой суммы по местам'
                    : 'График мест на выездах'
                }
              >
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={data.series}
                    margin={{ top: 12, right: 8, left: -20, bottom: 0 }}
                    onClick={(state) => {
                      if (data.granularity === 'day' && typeof state.activeLabel === 'string')
                        openDay(state.activeLabel);
                    }}
                  >
                    <defs>
                      <linearGradient id="money-fill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#b9d552" stopOpacity={0.55} />
                        <stop offset="100%" stopColor="#b9d552" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} stroke="var(--line)" strokeDasharray="3 5" />
                    <XAxis
                      dataKey="date"
                      axisLine={false}
                      tickLine={false}
                      minTickGap={38}
                      tick={{ fill: 'var(--muted)', fontSize: 12 }}
                      tickFormatter={(value) => chartDate(value, data.granularity)}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: 'var(--muted)', fontSize: 12 }}
                    />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 14,
                        border: '1px solid var(--line)',
                        background: 'var(--surface)',
                        color: 'var(--ink)',
                      }}
                      labelFormatter={(value) => chartDate(String(value), data.granularity)}
                      formatter={(value, name) => [
                        chart === 'money' ? money(Number(value)) : number(Number(value)),
                        name,
                      ]}
                    />
                    <Area
                      isAnimationActive={false}
                      type="monotone"
                      dataKey={chart === 'money' ? 'expected_gel' : 'capacity'}
                      name={chart === 'money' ? 'Ожидаемо по местам' : 'Вместимость'}
                      stroke="#9ca395"
                      fill="transparent"
                      strokeDasharray="4 5"
                      strokeWidth={1.5}
                    />
                    <Area
                      isAnimationActive={false}
                      type="monotone"
                      dataKey={chart === 'money' ? 'net_gel' : 'seats'}
                      name={chart === 'money' ? 'Отмеченные оплаты' : 'Занятые места'}
                      stroke="var(--chart-stroke)"
                      fill="url(#money-fill)"
                      strokeWidth={2.5}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className="chart-legend">
                <span>
                  <i className="legend-dot lime" />
                  {chart === 'money' ? 'Отмеченные оплаты' : 'Занятые места'}
                </span>
                <span>
                  <i className="legend-dash" />
                  {chart === 'money' ? 'Ожидаемо по местам' : 'Вместимость'}
                </span>
              </div>
              {data.granularity === 'day' && (
                <p className="caption">Нажми на дату графика, чтобы открыть день.</p>
              )}
            </section>
          )}
          {showLoad && detailed && !personal && data.demand ? (
            <DemandPanel
              data={data.demand}
              liveDays={liveDays}
              liveStatus={liveStatus}
              openDay={openDay}
            />
          ) : (
            showLoad && (
              <section className="panel load-panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">ПО ВРЕМЕНИ ВЫЕЗДА</span>
                    <h2>{personal ? 'Твои любимые выезды' : 'Где больше спроса'}</h2>
                  </div>
                  <Bike size={21} />
                </div>
                <div className="load-list">
                  {data.by_time.length ? (
                    data.by_time.map((row) => (
                      <div className="load-row" key={row.time}>
                        <div>
                          <strong>{row.time}</strong>
                          <span>
                            {number(row.seats)} мест · {row.lifts} выездов
                          </span>
                        </div>
                        <b>{number(row.occupancy_pct)}%</b>
                        <div className="load-track">
                          <div style={{ width: `${Math.min(row.occupancy_pct, 100)}%` }} />
                        </div>
                      </div>
                    ))
                  ) : (
                    <Empty text="В этом периоде ещё нет выездов." />
                  )}
                </div>
                <div className="load-note">
                  <span className="small-icon">
                    <ArrowUpRight size={18} />
                  </span>
                  <p>Загрузка считается по сохранённым результатам состоявшихся выездов.</p>
                </div>
              </section>
            )
          )}
        </div>
      )}
      {(showMoney || showPeople) && (
        <div className="two-columns finance-details">
          {showMoney && (
            <section className="panel payments-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">СТРУКТУРА ОПЛАТ</span>
                  <h2>Наличные и переводы</h2>
                </div>
                <Wallet size={21} />
              </div>
              <div className="money-breakdown">
                <div className="donut" role="img" aria-label="Доли способов оплаты">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        isAnimationActive={false}
                        data={methods}
                        dataKey="value"
                        innerRadius="64%"
                        outerRadius="86%"
                        paddingAngle={3}
                        stroke="none"
                      >
                        {methods.map((m) => (
                          <Cell key={m.name} fill={m.color} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(value) => money(Number(value))}
                        contentStyle={{
                          background: 'var(--surface)',
                          color: 'var(--ink)',
                          border: '1px solid var(--line)',
                          borderRadius: 12,
                        }}
                        itemStyle={{ color: 'var(--ink)' }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <span>
                    <b>{money(summary.net_gel)}</b>отмечено
                  </span>
                </div>
                <div className="money-methods">
                  {methods.length ? (
                    methods.map((m) => (
                      <div key={m.name}>
                        <span>
                          <i className="legend-dot" style={{ background: m.color }} />
                          {m.name}
                        </span>
                        <strong>{money(m.value)}</strong>
                      </div>
                    ))
                  ) : (
                    <Empty text="Оплаты пока не отмечены." />
                  )}
                </div>
              </div>
              <div className="financial-lines">
                <span>
                  Добавлено в журнал <b>{money(summary.received_gel)}</b>
                </span>
                <span>
                  Сторнировано в журнале <b>{money(summary.reversed_gel)}</b>
                </span>
                <span>
                  Ожидаемо по местам <b>{money(summary.expected_gel)}</b>
                </span>
              </div>
            </section>
          )}
          {showPeople && (
            <section className="panel participation-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">УЧАСТИЕ</span>
                  <h2>Каждое место имеет значение</h2>
                </div>
                <Users size={21} />
              </div>
              <div className="participation-number">
                {number(summary.seats)}
                <span>мест на выездах</span>
              </div>
              <div className="financial-lines">
                <span>
                  Гостевые места <b>{number(summary.guests)}</b>
                </span>
                <span>
                  Ручные места <b>{number(summary.manual)}</b>
                </span>
                <span>
                  Отменённые выезды <b>{number(summary.cancelled_lifts)}</b>
                </span>
              </div>
              <p className="caption">
                Райдер учитывается один раз за период. Места учитываются отдельно для каждого
                выезда.
              </p>
            </section>
          )}
        </div>
      )}
      {showHistory && <HistoryTable data={data} openDay={openDay} />}
      <p className="data-note">
        История с{' '}
        {data.first_record
          ? dateLabel(data.first_record, {
              day: 'numeric',
              month: 'long',
              year: 'numeric',
            })
          : 'первой сохранённой записи'}
        .{' '}
        {summary.backfilled_days > 0 &&
          `${summary.backfilled_days} дней восстановлено по поздним данным. `}
        Оплаты — зарегистрированные сообщения о получении денег; расходы и прибыль здесь не
        учитываются.
      </p>
    </div>
  );
}
export function HistoryTable({ data, openDay }: { data: Analytics; openDay(day: string): void }) {
  const [limit, setLimit] = useState(5);
  function exportCsv() {
    const rows = [
      ['Дата', 'Выезды', 'Места', 'Загрузка %', 'Оплаты GEL', 'Восстановлено'],
      ...data.days.map((d) => [
        d.date,
        d.lifts,
        d.seats,
        d.occupancy_pct,
        d.net_gel,
        d.backfilled_days,
      ]),
    ];
    const blob = new Blob(['\uFEFF' + rows.map((row) => row.join(';')).join('\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob),
      link = document.createElement('a');
    link.href = url;
    link.download = `veloexpress-${data.start}-${data.end}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="panel history-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">ДЕТАЛИ</span>
          <h2>Дни, из которых сложился период</h2>
        </div>
        <Button variant="ghost" size="sm" onClick={exportCsv} disabled={!data.days.length}>
          <Download size={16} /> CSV
        </Button>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>День выездов</th>
              <th>Выезды</th>
              <th>Места</th>
              <th>Загрузка</th>
              <th>Отмечено</th>
              <th>
                <span className="sr-only">Подробнее</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.days.slice(0, limit).map((day) => (
              <tr key={day.date}>
                <td>
                  <button className="text-button" onClick={() => openDay(day.date)}>
                    {dateLabel(day.date, {
                      weekday: 'short',
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })}
                    {day.backfilled_days > 0 && (
                      <span className="badge muted-badge">Восстановлено</span>
                    )}
                  </button>
                </td>
                <td>{day.lifts}</td>
                <td>
                  {day.seats}
                  <small> / {day.capacity}</small>
                </td>
                <td>
                  <span className="table-load">
                    <i style={{ width: `${Math.min(day.occupancy_pct, 100)}%` }} />
                  </span>
                  {number(day.occupancy_pct)}%
                </td>
                <td className="money-cell">{money(day.net_gel)}</td>
                <td>
                  <button
                    className="icon-link"
                    aria-label={`Открыть ${day.date}`}
                    onClick={() => openDay(day.date)}
                  >
                    <ChevronRight size={18} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data.days.length && (
        <Empty text="За этот период нет сохранённой истории. Попробуй другой период." />
      )}
      {data.days.length > limit && (
        <Button variant="ghost" className="show-more" onClick={() => setLimit(limit + 20)}>
          Показать ещё {Math.min(20, data.days.length - limit)} дней
        </Button>
      )}
    </section>
  );
}
export function RidersTable({ data }: { data: Analytics }) {
  const [search, setSearch] = useState(''),
    [selected, setSelected] = useState<number | null>(null);
  const riders = data.riders.filter((r) =>
    r.label.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  const current = data.riders.find((r) => r.user_id === selected);
  return (
    <>
      <div className="metrics-grid">
        <Metric
          title="Райдеров за период"
          value={number(data.summary.riders)}
          note="Каждый человек учтён один раз"
          icon={Users}
          accent
        />
        <Metric
          title="Первый раз в истории"
          value={number(data.summary.new_riders ?? 0)}
          note="По доступным сохранённым данным"
          icon={ArrowUpRight}
        />
        <Metric
          title="Гостевых мест"
          value={number(data.summary.guests)}
          note="Гости не входят в уникальных райдеров"
          icon={Users}
        />
        <Metric
          title="Выездов"
          value={number(data.summary.lifts)}
          note={`${data.summary.days} дней в периоде`}
          icon={Bike}
        />
      </div>
      <section className="panel">
        <div className="panel-heading">
          <h2>Люди нашего сообщества</h2>
          <input
            className="search-input"
            aria-label="Поиск участника"
            placeholder="Найти райдера…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Райдер</th>
                <th>Дни</th>
                <th>Выезды</th>
                <th>Гости</th>
                <th>Оплаты</th>
                <th>Последний день</th>
              </tr>
            </thead>
            <tbody>
              {riders.map((rider, i) => (
                <tr key={rider.user_id}>
                  <td>
                    <button
                      className="rider-label"
                      onClick={() => setSelected(selected === rider.user_id ? null : rider.user_id)}
                    >
                      <span className={`avatar avatar-${i % 3}`}>{rider.label.slice(0, 1)}</span>
                      {rider.label}
                      {rider.new && <span className="badge">Новый</span>}
                    </button>
                  </td>
                  <td>{rider.days}</td>
                  <td>{rider.lifts}</td>
                  <td>{rider.guests}</td>
                  <td className="money-cell">{money(rider.net_gel)}</td>
                  <td>{rider.last_day ? dateLabel(rider.last_day) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!riders.length && (
          <Empty text={search ? 'Участник не найден.' : 'В этом периоде нет участников.'} />
        )}
      </section>
      {current && (
        <section className="panel rider-summary">
          <h2>{current.label}</h2>
          <p>
            {current.days} дней · {current.lifts} выездов · {current.guests} гостевых мест
          </p>
          <p>
            Отмечено оплат за выбранный период: <strong>{money(current.net_gel)}</strong>.
          </p>
          <p className="caption">
            Это история участия по сохранённым местам, а не проверка фактической явки.
          </p>
        </section>
      )}
    </>
  );
}
