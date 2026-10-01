import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  Clock3,
  Minus,
  Plus,
  Send,
  Settings2,
  ShieldCheck,
  Users,
  Wallet,
  X,
} from 'lucide-react';
import { request } from '../api';
import { dateLabel, money, plain } from '../lib';
import type {
  CommandSpec,
  DayDetail,
  LiveDay,
  Planning,
  CommandResult,
  RefundReport,
} from '../types';
import { Button } from './ui/button';
import { Dialog } from './ui/dialog';
import { Select } from './ui/select';
import { Empty } from './Shared';
export type Act = (spec: CommandSpec) => void;
export function Departures({ days, openDay }: { days: LiveDay[]; openDay(day: string): void }) {
  return (
    <>
      <div className="section-intro">
        <span className="eyebrow">ОПЕРАТИВНАЯ КАРТИНА</span>
        <h2>Ближайшие дни</h2>
        <p>Кто едет, сколько мест осталось и как идут оплаты.</p>
      </div>
      <div className="departure-grid">
        {days.map((day) => (
          <button
            className="day-card"
            key={day.service_date}
            onClick={() => openDay(day.service_date)}
          >
            <div className="day-card-header">
              <span className="date-tile">
                <b>{dateLabel(day.service_date, { day: 'numeric' })}</b>
                <span>{dateLabel(day.service_date, { month: 'short' })}</span>
              </span>
              <div>
                <h3>{dateLabel(day.service_date, { weekday: 'long' })}</h3>
                <span>{day.running_count} выездов набрали минимум</span>
              </div>
              <ArrowUpRight size={22} />
            </div>
            <div className="mini-lifts">
              {day.lifts.map((lift) => (
                <div key={lift.time} className={lift.cancelled ? 'cancelled' : ''}>
                  <span>{lift.time}</span>
                  <b>
                    {lift.seat_count}
                    <small>/{lift.capacity}</small>
                  </b>
                  <i
                    style={
                      {
                        '--fill': `${Math.min((lift.seat_count / lift.capacity) * 100, 100)}%`,
                      } as React.CSSProperties
                    }
                  />
                  {lift.waiting_count > 0 && <em>+{lift.waiting_count} в очереди</em>}
                </div>
              ))}
            </div>
            <div className="day-card-footer">
              <span>
                <Users size={16} />
                {day.booked_rider_count} райдеров
              </span>
              <span>
                <Wallet size={16} />
                {money(day.expected_gel - day.owed_gel)} / {money(day.expected_gel)}
              </span>
            </div>
            {day.owed_gel > 0 && (
              <div className="attention-strip">
                Не отмечено оплат: {money(day.owed_gel)}
                <ArrowUpRight size={15} />
              </div>
            )}
          </button>
        ))}
      </div>
      {!days.length && (
        <section className="panel">
          <Empty text="Активных дней пока нет. Подготовь расписание в разделе «Планирование»." />
        </section>
      )}
    </>
  );
}
export function DayDialog({
  day,
  onClose,
  act,
  busy,
}: {
  day: string | null;
  onClose(): void;
  act: Act;
  busy: boolean;
}) {
  const detail = useQuery({
    queryKey: ['day', day],
    queryFn: () => request<DayDetail>(`/api/admin/days/${day}`),
    enabled: !!day,
    refetchInterval: 30_000,
  });
  return (
    <Dialog
      open={!!day}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={
        day
          ? dateLabel(day, {
              weekday: 'long',
              day: 'numeric',
              month: 'long',
              year: 'numeric',
            })
          : 'День выездов'
      }
      description="Места, участники и зарегистрированные оплаты."
    >
      {detail.isPending ? (
        <div className="skeleton tall" />
      ) : detail.isError ? (
        <div className="error-state">
          <p>{detail.error.message}</p>
          <Button variant="secondary" onClick={() => detail.refetch()}>
            Повторить
          </Button>
        </div>
      ) : detail.data?.historical ? (
        <HistoricalDetail detail={detail.data} />
      ) : (
        detail.data && <LiveDetail key={day} detail={detail.data} act={act} busy={busy} />
      )}
    </Dialog>
  );
}
function HistoricalDetail({ detail }: { detail: Extract<DayDetail, { historical: true }> }) {
  return (
    <>
      <div className="inline-notice">
        Завершённый день ·{' '}
        {detail.reconstructed ? 'Данные восстановлены позже' : 'Сохранённый результат'}
      </div>
      <div className="detail-metrics">
        <span>
          Добавлено оплат<strong>{money(detail.received_gel)}</strong>
        </span>
        <span>
          Сторнировано<strong>{money(detail.refunded_gel)}</strong>
        </span>
        <span>
          Цена места<strong>{money(detail.price_gel)}</strong>
        </span>
      </div>
      {detail.lifts.map((lift) => (
        <details className="lift-detail" key={lift.lift_time}>
          <summary>
            <strong>{lift.lift_time}</strong>
            <span>
              {lift.seats} / {lift.capacity} мест
            </span>
            <span className={`badge ${!lift.ran ? 'muted-badge' : ''}`}>
              {lift.ran ? 'Состоялся' : 'Не состоялся'}
            </span>
          </summary>
          <div className="lift-riders">
            {lift.riders.map((rider, i) => (
              <div key={i}>
                <span>{rider.label || 'Ручные места'}</span>
                <span>
                  {rider.seats} мест
                  {rider.guests ? ` · ${rider.guests} гостей` : ''}
                </span>
              </div>
            ))}
          </div>
        </details>
      ))}
    </>
  );
}
function LiveDetail({ detail, act, busy }: { detail: LiveDay; act: Act; busy: boolean }) {
  const [userId, setUserId] = useState(detail.riders?.[0]?.user_id ?? 0),
    [amount, setAmount] = useState('20'),
    [method, setMethod] = useState<'cash' | 'transfer'>('transfer');
  return (
    <>
      <div className="detail-metrics">
        <span>
          Райдеров<strong>{detail.booked_rider_count}</strong>
        </span>
        <span>
          Ожидаемо<strong>{money(detail.expected_gel)}</strong>
        </span>
        <span>
          Не отмечено<strong>{money(detail.owed_gel)}</strong>
        </span>
      </div>
      {detail.lifts.map((lift) => (
        <section className={`lift-detail ${lift.cancelled ? 'cancelled' : ''}`} key={lift.time}>
          <div className="lift-detail-heading">
            <strong>{lift.time}</strong>
            <span>
              {lift.seat_count} / {lift.capacity} мест
            </span>
            <span
              className={`badge ${lift.cancelled ? 'muted-badge' : lift.running ? '' : 'warning-badge'}`}
            >
              {lift.cancelled ? 'Отменён' : lift.running ? 'Набрал минимум' : 'Нужны райдеры'}
            </span>
          </div>
          {lift.waiting_count > 0 && <p className="caption">В очереди: {lift.waiting_count}</p>}
          <div className="lift-riders">
            {lift.riders?.map((rider) => (
              <div key={rider.telegram_user_id}>
                <span>
                  {rider.label}
                  {rider.guests > 0 && <small> +{rider.guests} гостей</small>}
                </span>
                <span
                  className={
                    rider.waitlisted ? 'warning-text' : rider.paid ? 'paid-text' : 'muted-text'
                  }
                >
                  {rider.waitlisted ? (
                    'В очереди'
                  ) : rider.paid ? (
                    <>
                      <Check size={14} />
                      {rider.cash ? 'Наличные' : 'Отмечено'}
                    </>
                  ) : (
                    'Не отмечено'
                  )}
                </span>
              </div>
            ))}
          </div>
          {!detail.past && (
            <div className="lift-actions">
              <span>Ручные места</span>
              <Button
                variant="secondary"
                size="icon"
                aria-label={`Убрать ручное место ${lift.time}`}
                disabled={busy || lift.manual_count === 0}
                onClick={() =>
                  act({
                    action: 'manual',
                    service_date: detail.service_date,
                    lift_time: lift.time,
                    delta: -1,
                  })
                }
              >
                <Minus size={15} />
              </Button>
              <b>{lift.manual_count}</b>
              <Button
                variant="secondary"
                size="icon"
                aria-label={`Добавить ручное место ${lift.time}`}
                disabled={busy || lift.cancelled || lift.seat_count >= lift.capacity}
                onClick={() =>
                  act({
                    action: 'manual',
                    service_date: detail.service_date,
                    lift_time: lift.time,
                    delta: 1,
                  })
                }
              >
                <Plus size={15} />
              </Button>
              <Button
                variant={lift.cancelled ? 'secondary' : 'ghost'}
                size="sm"
                disabled={busy}
                onClick={() =>
                  act({
                    action: lift.cancelled ? 'restore_lift' : 'cancel_lift',
                    service_date: detail.service_date,
                    lift_time: lift.time,
                  })
                }
              >
                {lift.cancelled ? 'Восстановить' : 'Отменить выезд'}
              </Button>
            </div>
          )}
        </section>
      ))}
      {!!detail.riders?.length && (
        <section className="payment-form">
          <h3>
            <Wallet size={18} /> Отметить полученную оплату
          </h3>
          <p className="caption">
            Укажи сумму, которую уже получил. Она добавится к текущей оплате участника.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act({
                action: 'payment',
                service_date: detail.service_date,
                user_id: userId,
                amount_gel: Number(amount),
                method,
              });
            }}
          >
            <label>
              Участник
              <Select<number>
                label="Участник"
                value={userId}
                onValueChange={setUserId}
                options={detail.riders.map((r) => ({
                  value: r.user_id,
                  label: `${r.label} · отмечено ${r.paid_gel} ₾ / ${r.due_now_gel} ₾`,
                }))}
              />
            </label>
            <div className="form-row">
              <label>
                Получено, GEL
                <input
                  type="number"
                  min="1"
                  max="100000"
                  step="1"
                  required
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </label>
              <label>
                Способ
                <Select<'cash' | 'transfer'>
                  label="Способ"
                  value={method}
                  onValueChange={setMethod}
                  options={[
                    { value: 'transfer', label: 'Перевод' },
                    { value: 'cash', label: 'Наличные' },
                  ]}
                />
              </label>
            </div>
            <Button disabled={busy || !userId || Number(amount) <= 0}>
              <Plus size={16} />
              Добавить оплату
            </Button>
          </form>
        </section>
      )}
      {!!detail.late_exits.length && (
        <div className="inline-notice">
          Поздние отмены: {detail.late_exits.map((r) => `${r.label} · ${r.lift_time}`).join(', ')}.
          Требуют решения администратора.
        </div>
      )}
      {!detail.past && (
        <div className="danger-zone">
          <p>Отмена дня закрывает опросы и сохраняет оценку возвратов.</p>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => act({ action: 'cancel_day', service_date: detail.service_date })}
          >
            Отменить весь день
          </Button>
        </div>
      )}
    </>
  );
}
export function PlanningView({
  data,
  act,
  busy,
  today,
}: {
  data: Planning;
  act: Act;
  busy: boolean;
  today: string;
}) {
  const [section, setSection] = useState('weekend');
  const [sat, setSat] = useState(data.saturday_enabled),
    [sun, setSun] = useState(data.sunday_enabled);
  const [first, setFirst] = useState(data.first_lift_time),
    [last, setLast] = useState(data.last_lift_time);
  const [price, setPrice] = useState(String(data.terms.price_gel)),
    [deadline, setDeadline] = useState(data.terms.deadline_time);
  const [enabled, setEnabled] = useState(data.schedule.enabled),
    [weekday, setWeekday] = useState(data.schedule.creation_weekday),
    [creation, setCreation] = useState(data.schedule.creation_time),
    [lead, setLead] = useState(data.schedule.announce_lead_minutes);
  const [extra, setExtra] = useState(today),
    [extraFirst, setExtraFirst] = useState('8:30'),
    [extraLast, setExtraLast] = useState('13:30');
  const endDate = new Date(`${today}T12:00:00Z`);
  endDate.setUTCDate(endDate.getUTCDate() + 7);
  const locked = data.posted_dates.some(
    (day) =>
      day === data.week_start ||
      day ===
        new Date(new Date(`${data.week_start}T12:00:00Z`).getTime() + 86400000)
          .toISOString()
          .slice(0, 10),
  );
  return (
    <div className="planning-workspace">
      <div className="planning-tabs" role="group" aria-label="Раздел планирования">
        {[
          ['weekend', 'Выходные'],
          ['schedule', 'Публикация'],
          ['terms', 'Условия'],
          ['extra', '+ День'],
        ].map(([key, label]) => (
          <button key={key} aria-pressed={section === key} onClick={() => setSection(key)}>
            {label}
          </button>
        ))}
      </div>
      <div className="planning-grid">
        <section className="panel weekend-plan" data-planning-active={section === 'weekend'}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">СЛЕДУЮЩИЕ ВЫХОДНЫЕ</span>
              <h2>{dateLabel(data.week_start, { day: 'numeric', month: 'long' })}</h2>
            </div>
            <CalendarDays size={24} />
          </div>
          <p className="caption">
            Подготовь дни и диапазон выездов. Сохранение плана не публикует опросы.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act({
                action: 'plan',
                service_date: data.week_start,
                saturday_enabled: sat,
                sunday_enabled: sun,
                first_lift_time: first,
                last_lift_time: last,
              });
            }}
          >
            <div className="day-toggles">
              <label className={sat ? 'selected' : ''}>
                <input
                  type="checkbox"
                  checked={sat}
                  disabled={locked || busy}
                  onChange={(e) => setSat(e.target.checked)}
                />
                <span>Суббота</span>
                <Check size={18} />
              </label>
              <label className={sun ? 'selected' : ''}>
                <input
                  type="checkbox"
                  checked={sun}
                  disabled={locked || busy}
                  onChange={(e) => setSun(e.target.checked)}
                />
                <span>Воскресенье</span>
                <Check size={18} />
              </label>
            </div>
            <div className="form-row">
              <label>
                Первый выезд
                <Select<string>
                  label="Первый выезд"
                  value={first}
                  disabled={locked || busy}
                  onValueChange={setFirst}
                  options={data.lift_times.map((time) => ({ value: time, label: time }))}
                />
              </label>
              <label>
                Последний выезд
                <Select<string>
                  label="Последний выезд"
                  value={last}
                  disabled={locked || busy}
                  onValueChange={setLast}
                  options={data.lift_times.map((time) => ({ value: time, label: time }))}
                />
              </label>
            </div>
            {locked ? (
              <div className="inline-notice">
                <ShieldCheck size={18} />
                Опубликованный план защищён от изменений.
              </div>
            ) : (
              <Button variant="secondary" disabled={busy || (!sat && !sun)}>
                Сохранить план
              </Button>
            )}
          </form>
          <div className="planning-publish">
            <Button disabled={busy} onClick={() => act({ action: 'post' })}>
              <Send size={16} />
              Опубликовать сейчас
            </Button>
            <Button
              variant="ghost"
              disabled={busy || !data.schedule.enabled}
              onClick={() => act({ action: 'skip' })}
            >
              {data.schedule.skip_week_start
                ? 'Вернуть автопубликацию'
                : 'Пропустить автопубликацию'}
            </Button>
          </div>
        </section>
        <section className="panel" data-planning-active={section === 'terms'}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">УСЛОВИЯ НОВЫХ ДНЕЙ</span>
              <h2>Цена и дедлайн</h2>
            </div>
            <Settings2 size={23} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act({
                action: 'terms',
                price_gel: Number(price),
                deadline_time: deadline,
              });
            }}
          >
            <div className="form-row">
              <label>
                Цена места, GEL
                <input
                  type="number"
                  min="1"
                  max="10000"
                  step="1"
                  required
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                />
              </label>
              <label>
                Дедлайн накануне
                <input
                  type="time"
                  required
                  value={deadline}
                  onChange={(e) => setDeadline(e.target.value)}
                />
              </label>
            </div>
            <div className="inline-notice">
              Применяется к дням, опубликованным после изменения. Уже опубликованные сохраняют свои
              условия.
            </div>
            <Button variant="secondary" disabled={busy || Number(price) <= 0}>
              Сохранить условия
            </Button>
          </form>
          <p className="caption">Часовой пояс: {data.terms.timezone}</p>
        </section>
        <section className="panel" data-planning-active={section === 'schedule'}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">АВТОМАТИЧЕСКИ</span>
              <h2>Когда открывать опросы</h2>
            </div>
            <Clock3 size={23} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act({
                action: 'schedule',
                enabled,
                creation_weekday: weekday,
                creation_time: creation,
                announce_lead_minutes: lead,
              });
            }}
          >
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Публиковать автоматически
            </label>
            <div className="form-row">
              <label>
                День недели
                <Select<number>
                  label="День недели"
                  value={weekday}
                  onValueChange={setWeekday}
                  options={['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'].map(
                    (label, value) => ({ label, value }),
                  )}
                />
              </label>
              <label>
                Время
                <input
                  type="time"
                  required
                  value={creation}
                  onChange={(e) => setCreation(e.target.value)}
                />
              </label>
            </div>
            <label>
              Напоминание группе
              <Select<number>
                label="Напоминание группе"
                value={lead}
                onValueChange={setLead}
                options={[
                  { value: 0, label: 'Без напоминания' },
                  { value: 60, label: 'За 1 час' },
                  { value: 120, label: 'За 2 часа' },
                  { value: 180, label: 'За 3 часа' },
                ]}
              />
            </label>
            <Button variant="secondary" disabled={busy}>
              Сохранить расписание
            </Button>
          </form>
        </section>
        <section className="panel" data-planning-active={section === 'extra'}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">ВНЕ РАСПИСАНИЯ</span>
              <h2>Дополнительный день</h2>
            </div>
            <Plus size={24} />
          </div>
          <p className="caption">
            Создать выезды на дату в ближайшие семь дней. Опрос появится в группе после
            подтверждения.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act({
                action: 'extra',
                service_date: extra,
                first_lift_time: extraFirst,
                last_lift_time: extraLast,
              });
            }}
          >
            <label>
              Дата
              <input
                type="date"
                min={today}
                max={endDate.toISOString().slice(0, 10)}
                required
                value={extra}
                onChange={(e) => setExtra(e.target.value)}
              />
            </label>
            <div className="form-row">
              <label>
                Первый выезд
                <Select<string>
                  label="Первый выезд"
                  value={extraFirst}
                  onValueChange={setExtraFirst}
                  options={data.lift_times.map((time) => ({ value: time, label: time }))}
                />
              </label>
              <label>
                Последний выезд
                <Select<string>
                  label="Последний выезд"
                  value={extraLast}
                  onValueChange={setExtraLast}
                  options={data.lift_times.map((time) => ({ value: time, label: time }))}
                />
              </label>
            </div>
            <Button variant="secondary" disabled={busy}>
              <Plus size={16} />
              Создать день
            </Button>
          </form>
        </section>
      </div>
    </div>
  );
}
const actions: Record<string, string> = {
  manual: 'Ручные места',
  payment: 'Отметка оплаты',
  cancel_lift: 'Отмена выезда',
  restore_lift: 'Восстановление выезда',
  cancel_day: 'Отмена дня',
  terms: 'Цена и дедлайн',
  plan: 'План выходных',
  post: 'Публикация опросов',
  extra: 'Дополнительный день',
  schedule: 'Расписание публикации',
  skip: 'Пропуск автопубликации',
  claim_payment: 'Сообщение об оплате',
  guest: 'Гостевые места',
  undo_payment: 'Отмена сообщения об оплате',
};
const statuses = {
  pending: 'Ожидает бота',
  running: 'Выполняется',
  complete: 'Готово',
  failed: 'Отклонено',
  review: 'Проверь результат',
};
export function AuditView({ entries, timezone }: { entries: CommandResult[]; timezone: string }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">ПРОЗРАЧНОСТЬ ДЕЙСТВИЙ</span>
          <h2>Журнал кабинета</h2>
        </div>
        <ShieldCheck size={22} />
      </div>
      <p className="caption">
        Запросы из кабинета, их авторы и результаты. Операции с неопределённым результатом требуют
        проверки.
      </p>
      <div className="audit-list">
        {entries.map((entry) => (
          <div key={entry.id}>
            <span className={`audit-icon ${entry.status === 'complete' ? '' : 'audit-pending'}`}>
              {entry.status === 'complete' ? <Check size={18} /> : <Clock3 size={18} />}
            </span>
            <div>
              <strong>{actions[entry.action] ?? entry.action}</strong>
              <p>{entry.result ? plain(entry.result.message) : `Запрос #${entry.id}`}</p>
              <small>
                {new Date(entry.created_at).toLocaleString('ru-RU', {
                  timeZone: timezone,
                })}{' '}
                · {entry.actor_name ?? `Telegram ID ${entry.actor_user_id}`}
              </small>
            </div>
            <span
              className={`badge ${entry.status === 'failed' || entry.status === 'review' ? 'warning-badge' : ''}`}
            >
              {statuses[entry.status]}
            </span>
          </div>
        ))}
      </div>
      {!entries.length && <Empty text="Здесь появятся действия, выполненные через кабинет." />}
    </section>
  );
}
export function RefundsView({ reports }: { reports: RefundReport[] }) {
  return (
    <>
      <div className="inline-notice">
        Это сохранённые оценки при отменах. Они не подтверждают, что деньги были возвращены.
      </div>
      <div className="two-columns">
        {reports.map((report, i) => (
          <section className="panel" key={i}>
            <div className="panel-heading">
              <h2>
                {dateLabel(report.service_date)}
                {report.lift_time ? ` · ${report.lift_time}` : ' · весь день'}
              </h2>
              <ArrowUpRight size={20} />
            </div>
            <pre className="report-text">{plain(report.text)}</pre>
          </section>
        ))}
      </div>
      {!reports.length && (
        <section className="panel">
          <Empty text="Сохранённых оценок возвратов пока нет." />
        </section>
      )}
    </>
  );
}
