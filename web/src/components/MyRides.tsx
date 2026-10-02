import { useState } from 'react';
import { Bike, Check, ChevronDown, ExternalLink, Minus, Plus, Users, Wallet } from 'lucide-react';
import { dateLabel, money, plain } from '../lib';
import { openTelegram } from '../telegram';
import type { MyDays } from '../types';
import type { Act } from './Operations';
import { Button } from './ui/button';
import { Empty } from './Shared';

export function MyRides({ data, act, busy }: { data: MyDays; act: Act; busy: boolean }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [guestLift, setGuestLift] = useState<string | null>(null);
  const first =
    data.days.find((day) => !day.past && day.booking?.rows.length) ??
    data.days.find((day) => !day.past) ??
    data.days[0];
  const day = data.days.find((day) => day.service_date === selectedDate) ?? first;
  const booking = day?.booking;
  const remaining = booking ? Math.max(booking.due_now_gel - booking.paid_gel, 0) : 0;
  const recordPayment = (method: 'cash' | 'transfer') =>
    day &&
    act({
      action: 'claim_payment',
      service_date: day.service_date,
      method,
    });
  const paymentButtons = (
    <div className="payment-buttons">
      <Button disabled={busy} onClick={() => recordPayment('transfer')}>
        <Check size={16} />Я перевёл
      </Button>
      <Button variant="secondary" disabled={busy} onClick={() => recordPayment('cash')}>
        Передал наличные
      </Button>
    </div>
  );
  return (
    <div className="rides-workspace">
      {data.days.length > 1 && (
        <div className="ride-day-tabs" role="group" aria-label="День поездок">
          {data.days.map((item) => (
            <button
              key={item.service_date}
              aria-pressed={day?.service_date === item.service_date}
              onClick={() => {
                setSelectedDate(item.service_date);
                setGuestLift(null);
              }}
            >
              {dateLabel(item.service_date, { weekday: 'short', day: 'numeric', month: 'short' })}
            </button>
          ))}
        </div>
      )}
      {day ? (
        <section className="panel my-day" key={day.service_date}>
          <div className="panel-heading ride-heading">
            <h2>
              {dateLabel(day.service_date, { weekday: 'long', day: 'numeric', month: 'long' })}
            </h2>
            <span className="badge muted-badge">{day.past ? 'Прошедший день' : 'Мои места'}</span>
          </div>
          {booking?.rows.length ? (
            <>
              <div className="my-bookings compact-bookings">
                {booking.rows.map((row) => (
                  <div key={row.lift_time}>
                    <div className="ride-row-line">
                      <strong>{row.lift_time}</strong>
                      <span
                        className={`badge ${row.waitlist_position || !row.running ? 'warning-badge' : ''}`}
                      >
                        {row.waitlist_position
                          ? `Очередь · #${row.waitlist_position}`
                          : row.running
                            ? 'Место занято'
                            : 'Ждём минимум'}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="guest-toggle"
                        aria-label={`Гости на выезд ${row.lift_time}`}
                        aria-expanded={guestLift === row.lift_time}
                        aria-controls={`guests-${day.service_date}-${row.lift_time}`}
                        onClick={() =>
                          setGuestLift(guestLift === row.lift_time ? null : row.lift_time)
                        }
                      >
                        <Users size={15} />
                        Гости{row.guests ? ` · ${row.guests}` : ''}
                        <ChevronDown size={13} />
                      </Button>
                    </div>
                    {guestLift === row.lift_time && (
                      <div
                        className="guest-controls"
                        id={`guests-${day.service_date}-${row.lift_time}`}
                      >
                        <span>Гостевые места</span>
                        <Button
                          variant="secondary"
                          size="icon"
                          aria-label={`Убрать гостя ${row.lift_time}`}
                          disabled={busy || !row.guests || day.past}
                          onClick={() =>
                            act({
                              action: 'guest',
                              service_date: day.service_date,
                              lift_time: row.lift_time,
                              delta: -1,
                            })
                          }
                        >
                          <Minus size={14} />
                        </Button>
                        <b>{row.guests}</b>
                        <Button
                          variant="secondary"
                          size="icon"
                          aria-label={`Добавить гостя ${row.lift_time}`}
                          disabled={busy || !row.seats_left || day.past}
                          onClick={() =>
                            act({
                              action: 'guest',
                              service_date: day.service_date,
                              lift_time: row.lift_time,
                              delta: 1,
                            })
                          }
                        >
                          <Plus size={14} />
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="ride-money">
                <span>
                  За выезды<strong>{money(booking.due_now_gel)}</strong>
                </span>
                <span>
                  Отмечено<strong>{money(booking.paid_gel)}</strong>
                </span>
                <span>
                  Осталось<strong>{money(remaining)}</strong>
                </span>
              </div>
              {booking.pending_lift_times.length > 0 && (
                <p className="ride-pending">
                  Ждём минимум: {booking.pending_lift_times.join(', ')}.
                </p>
              )}
              {remaining > 0 ? (
                <div className="payment-actions">
                  {paymentButtons}
                  <p className="caption">
                    Наличными — Мишо на месте в день поездки. Отмечай оплату только после передачи
                    денег.
                  </p>
                </div>
              ) : (
                <div className="ride-payment-status">
                  <Check size={16} />
                  {booking.paid_gel > 0 ? 'Оплата отмечена' : 'Пока ничего к оплате'}
                </div>
              )}
              <details className="ride-payment-details">
                <summary>
                  <Wallet size={16} />
                  <span>Оплата и реквизиты</span>
                  <ChevronDown size={15} />
                </summary>
                <div className="ride-payment-content">
                  <p className="caption">
                    {money(booking.price_gel)} за место. Сейчас к оплате только выезды, набравшие
                    минимум. За весь день: {money(booking.due_all_gel)}.
                  </p>
                  <pre className="report-text">{plain(data.bank_details)}</pre>
                  {remaining === 0 && <div className="payment-actions">{paymentButtons}</div>}
                  {booking.paid_gel > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="undo-payment"
                      disabled={busy}
                      onClick={() =>
                        act({ action: 'undo_payment', service_date: day.service_date })
                      }
                    >
                      Отменить свою отметку
                    </Button>
                  )}
                  <p className="caption">
                    Отмена отметки зависит от дедлайна и подтверждения админом.
                  </p>
                </div>
              </details>
            </>
          ) : (
            <>
              <p className="caption">На этот день у тебя пока нет бронирований.</p>
              <div className="available-lifts">
                {day.lifts.map((lift) => (
                  <div key={lift.time}>
                    <strong>{lift.time}</strong>
                    <span>
                      {lift.cancelled
                        ? 'Отменён'
                        : `${Math.max(lift.capacity - lift.seats, 0)} свободных мест`}
                    </span>
                  </div>
                ))}
              </div>
              {data.polls_url && (
                <Button variant="secondary" onClick={() => openTelegram(data.polls_url!)}>
                  Записаться в опросе
                  <ExternalLink size={16} />
                </Button>
              )}
            </>
          )}
        </section>
      ) : (
        <section className="panel">
          <Empty text="Ближайшие дни ещё не опубликованы. Новые опросы появятся в Telegram-группе." />
        </section>
      )}
      <details className="ride-help">
        <summary>
          <Bike size={16} />
          <span>Как записаться и оплатить</span>
          <ChevronDown size={15} />
        </summary>
        <div>
          <p className="caption">
            Запись и изменение выбранных выездов — в Telegram-опросе. Здесь можно добавить гостей и
            отметить оплату.
          </p>
          <pre className="report-text">{plain(data.bank_details)}</pre>
          {data.polls_url && (
            <Button variant="ghost" onClick={() => openTelegram(data.polls_url!)}>
              Открыть Telegram-группу
              <ExternalLink size={15} />
            </Button>
          )}
        </div>
      </details>
    </div>
  );
}
