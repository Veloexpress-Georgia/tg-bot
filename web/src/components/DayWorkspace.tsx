import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Check, Clock3, Minus, Plus, Users, Wallet } from 'lucide-react';
import { dayAttention } from '../day-workspace';
import { money } from '../lib';
import type { LiveDay } from '../types';
import type { Act } from './Operations';
import { AuditView } from './Audit';
import { Empty } from './Shared';
import { Button } from './ui/button';
import { Select } from './ui/select';

type Section = 'attention' | 'lifts' | 'payments' | 'activity';
export function DayWorkspace({
  detail,
  act,
  busy,
  timezone,
}: {
  detail: LiveDay;
  act: Act;
  busy: boolean;
  timezone: string;
}) {
  const [section, setSection] = useState<Section>('attention');
  const [time, setTime] = useState(detail.lifts[0]?.time ?? '');
  const [filter, setFilter] = useState('all');
  const [userId, setUserId] = useState(detail.riders?.[0]?.user_id ?? 0);
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState<'cash' | 'transfer'>('transfer');
  const [paymentFilter, setPaymentFilter] = useState<'all' | 'unpaid'>('all');
  const root = useRef<HTMLDivElement>(null);
  const resetScroll = () => root.current?.closest('.dialog-body')?.scrollTo({ top: 0 });
  useEffect(() => {
    root.current?.closest('.dialog-body')?.scrollTo({ top: 0 });
  }, [section]);
  const attention = dayAttention(detail);
  const lift = detail.lifts.find((l) => l.time === time);
  const selectedRider = detail.riders?.find((r) => r.user_id === userId);
  const focusLift = (value: string, nextFilter = 'all') => {
    setTime(value);
    setFilter(nextFilter);
    setSection('lifts');
  };
  const focusPayment = (uid: number, unpaidOnly = false) => {
    setUserId(uid);
    setAmount('');
    setMethod(detail.riders?.find((r) => r.user_id === uid)?.cash_on_site ? 'cash' : 'transfer');
    setSection('payments');
    if (unpaidOnly) setPaymentFilter('unpaid');
    resetScroll();
  };
  return (
    <div className="day-workspace" ref={root}>
      {(section === 'attention' || section === 'lifts') && (
        <div className="detail-metrics">
          <span>
            Райдеров<strong>{detail.booked_rider_count}</strong>
          </span>
          <span>
            Отмечено<strong>{money(detail.expected_gel)}</strong>
          </span>
          <span>
            По местам<strong>{money(detail.owed_gel)}</strong>
          </span>
        </div>
      )}
      <div className="workspace-tabs" role="group" aria-label="Раздел дня">
        {(
          [
            ['attention', 'Сейчас'],
            ['lifts', 'Выезды'],
            ['payments', 'Оплаты'],
            ['activity', 'Запросы'],
          ] as const
        ).map(([key, label]) => (
          <button key={key} aria-pressed={section === key} onClick={() => setSection(key)}>
            {label}
          </button>
        ))}
      </div>
      {section === 'attention' && (
        <div className="day-attention">
          <div className="workspace-heading">
            <h3>Что требует внимания</h3>
            <span>Обновляется каждые 30 сек.</span>
          </div>
          {!attention.queues.length &&
            !attention.underfilled.length &&
            !attention.unpaid.length &&
            !attention.lateExits.length &&
            !attention.commands.length && (
              <Empty text="Очередей и незавершённых вопросов нет. Выезды готовы." />
            )}
          {attention.commands.length > 0 && (
            <button className="attention-item" onClick={() => setSection('activity')}>
              <Clock3 size={20} />
              <span>
                <strong>Проверить запросы · {attention.commands.length}</strong>
                <small>Ожидают бота, завершились ошибкой или требуют проверки результата.</small>
              </span>
              <ArrowUpRight size={18} />
            </button>
          )}
          {attention.queues.map((l) => (
            <button
              className="attention-item"
              key={`queue-${l.time}`}
              onClick={() => focusLift(l.time, 'queue')}
            >
              <Users size={20} />
              <span>
                <strong>
                  {l.time} · в очереди {l.waiting_count}
                </strong>
                <small>Открыть список ожидающих и текущие места.</small>
              </span>
              <ArrowUpRight size={18} />
            </button>
          ))}
          {attention.underfilled.map((l) => (
            <button
              className="attention-item"
              key={`load-${l.time}`}
              onClick={() => focusLift(l.time)}
            >
              <Users size={20} />
              <span>
                <strong>{l.time} · минимум ещё не набран</strong>
                <small>
                  {l.seat_count} из {l.capacity} мест. Проверить участников и управление выездом.
                </small>
              </span>
              <ArrowUpRight size={18} />
            </button>
          ))}
          {attention.unpaid.length > 0 && (
            <section className="attention-group">
              <h3>Оплаты не отмечены · {attention.unpaid.length}</h3>
              {attention.unpaid.slice(0, 3).map((r) => (
                <button
                  className="attention-item"
                  key={r.user_id}
                  onClick={() => focusPayment(r.user_id, true)}
                >
                  <Wallet size={20} />
                  <span>
                    <strong>{r.label}</strong>
                    <small>Не отмечено {money(r.due_now_gel - r.paid_gel)}</small>
                  </span>
                  <ArrowUpRight size={18} />
                </button>
              ))}
              {attention.unpaid.length > 3 && (
                <Button
                  variant="secondary"
                  onClick={() => focusPayment(attention.unpaid[0].user_id, true)}
                >
                  Все участники без отметки · {attention.unpaid.length}
                </Button>
              )}
            </section>
          )}
          {attention.cashOnSite.length > 0 && (
            <section className="attention-group">
              <h3>Наличными на месте · {attention.cashOnSite.length}</h3>
              {attention.cashOnSite.map((r) => (
                <button
                  className="attention-item"
                  key={r.user_id}
                  onClick={() => focusPayment(r.user_id)}
                >
                  <Wallet size={20} />
                  <span>
                    <strong>{r.label}</strong>
                    <small>Обещал оплатить на месте. Получение денег ещё не подтверждено.</small>
                  </span>
                  <ArrowUpRight size={18} />
                </button>
              ))}
            </section>
          )}
          {attention.lateExits.length > 0 && (
            <section className="attention-group">
              <h3>Поздние отмены · {attention.lateExits.length}</h3>
              {attention.lateExits.map((r) => (
                <button
                  className="attention-item"
                  key={`${r.telegram_user_id}-${r.lift_time}`}
                  onClick={() => focusLift(r.lift_time)}
                >
                  <Clock3 size={20} />
                  <span>
                    <strong>
                      {r.label} · {r.lift_time}
                    </strong>
                    <small>Снял бронирование после дедлайна. Требует решения администратора.</small>
                  </span>
                  <ArrowUpRight size={18} />
                </button>
              ))}
            </section>
          )}
          <p className="caption">
            Отметки оплат — сообщения о получении денег. Кабинет не проверяет банковские переводы.
          </p>
        </div>
      )}
      {section === 'lifts' && (
        <>
          <div className="lift-picker" role="group" aria-label="Выезд дня">
            {detail.lifts.map((l) => (
              <button
                key={l.time}
                aria-pressed={time === l.time}
                onClick={() => {
                  setTime(l.time);
                  setFilter('all');
                }}
              >
                <strong>{l.time}</strong>
                <span>
                  {l.cancelled ? 'Отменён' : `${l.seat_count} / ${l.capacity}`}
                  {!l.cancelled && l.waiting_count > 0 ? ` · +${l.waiting_count}` : ''}
                </span>
              </button>
            ))}
          </div>
          {lift && (
            <section className="lift-detail">
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
              <div className="workspace-tabs small-tabs" role="group" aria-label="Участники выезда">
                {[
                  ['all', 'Все'],
                  ['unpaid', 'Без отметки'],
                  ['queue', 'Очередь'],
                ].map(([key, label]) => (
                  <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="lift-riders">
                {(lift.riders ?? [])
                  .filter(
                    (r) =>
                      filter === 'all' ||
                      (filter === 'queue' ? r.waitlisted : !r.paid && !r.waitlisted),
                  )
                  .map((r) => (
                    <div key={r.telegram_user_id}>
                      <span>
                        {r.label}
                        {r.guests > 0 && <small> · гостей {r.guests}</small>}
                      </span>
                      <span
                        className={
                          r.waitlisted ? 'warning-text' : r.paid ? 'paid-text' : 'muted-text'
                        }
                      >
                        {r.waitlisted ? (
                          'В очереди'
                        ) : r.paid ? (
                          <>
                            <Check size={14} />
                            {r.cash ? 'Наличные' : 'Отмечено'}
                          </>
                        ) : (
                          'Не отмечено'
                        )}
                      </span>
                    </div>
                  ))}
              </div>
              {!(lift.riders ?? []).some(
                (r) =>
                  filter === 'all' ||
                  (filter === 'queue' ? r.waitlisted : !r.paid && !r.waitlisted),
              ) && <p className="caption">В этом списке пока нет участников.</p>}
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
          )}
          {!detail.past && (
            <details className="day-management">
              <summary>Управление днём</summary>
              <div className="danger-zone">
                <p>
                  Отмена дня закрывает опросы и сохраняет оценку возвратов. Перед выполнением
                  появится подтверждение.
                </p>
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => act({ action: 'cancel_day', service_date: detail.service_date })}
                >
                  Отменить весь день
                </Button>
              </div>
            </details>
          )}
        </>
      )}
      {section === 'payments' && (
        <>
          {!detail.riders?.length ? (
            <Empty text="Нет участников, для которых можно отметить оплату." />
          ) : (
            <section className="payment-form">
              <h3>
                <Wallet size={18} /> Отметить полученную оплату
              </h3>
              <p className="caption">
                Сумма добавится к уже отмеченной оплате. Укажи, сколько действительно получил.
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
                    onValueChange={(uid) => {
                      setUserId(uid);
                      setAmount('');
                    }}
                    options={detail.riders.map((r) => ({ value: r.user_id, label: r.label }))}
                  />
                </label>
                {selectedRider && (
                  <div className="payment-context">
                    {selectedRider.cash_on_site && (
                      <p>
                        Выбрана оплата наличными на месте. Отмечай получение после передачи денег.
                      </p>
                    )}
                    <span>
                      Уже отмечено <strong>{money(selectedRider.paid_gel)}</strong>
                    </span>
                    <span>
                      Не отмечено{' '}
                      <strong>
                        {money(Math.max(selectedRider.due_now_gel - selectedRider.paid_gel, 0))}
                      </strong>
                    </span>
                    {selectedRider.pending_lift_times.length > 0 && (
                      <p>
                        Выезды без минимума: {selectedRider.pending_lift_times.join(', ')}. Сумма по
                        всем бронированиям: {money(selectedRider.due_all_gel)}.
                      </p>
                    )}
                  </div>
                )}
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
          <div className="workspace-heading">
            <h3>Отметки участников</h3>
            <span>
              {paymentFilter === 'unpaid'
                ? `${attention.unpaid.length} без отметки`
                : `${detail.riders?.length ?? 0} участников`}
            </span>
          </div>
          <div className="workspace-tabs small-tabs" role="group" aria-label="Фильтр оплат">
            {(
              [
                ['all', 'Все'],
                ['unpaid', 'Не отмечено'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                aria-pressed={paymentFilter === key}
                onClick={() => setPaymentFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="day-payment-list">
            {(detail.riders ?? [])
              .filter((r) => paymentFilter === 'all' || r.due_now_gel > r.paid_gel)
              .map((r) => (
                <button
                  key={r.user_id}
                  className={r.user_id === userId ? 'selected' : ''}
                  onClick={() => focusPayment(r.user_id)}
                >
                  <span>
                    <strong>{r.label}</strong>
                    <small>Отмечено {money(r.paid_gel)}</small>
                    {r.cash_on_site && <small>Заплатит наличными на месте</small>}
                  </span>
                  <span>
                    {r.due_now_gel > r.paid_gel ? (
                      <b className="warning-text">Ещё {money(r.due_now_gel - r.paid_gel)}</b>
                    ) : r.due_now_gel === 0 ? (
                      <b className="muted-text">Нет суммы к оплате</b>
                    ) : (
                      <b className="paid-text">Отмечено</b>
                    )}
                    <ArrowUpRight size={16} />
                  </span>
                </button>
              ))}
          </div>
        </>
      )}
      {section === 'activity' && (
        <>
          <p className="inline-notice">
            Статус «Проверь результат» означает, что действие могло выполниться частично. Проверь
            день и Telegram перед новым запросом.
          </p>
          <AuditView entries={detail.commands ?? []} timezone={timezone} />
        </>
      )}
    </div>
  );
}
