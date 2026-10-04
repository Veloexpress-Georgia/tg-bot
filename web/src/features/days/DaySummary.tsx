import { Banknote, Check, ChevronRight, CircleAlert, Clock3, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { t } from '../../i18n';
import { clockMinutes, cx, money } from '../../lib';
import type { LiveDay } from '../../types';
import { Badge, Card, List, Meter, Row, Section } from '../../ui';
import {
  activeLiftCount,
  dayMoney,
  liftState,
  liftStateLabel,
  liftTone,
  runningCount,
  type dayAttention,
} from './model';
import { withdrawalTime } from './WithdrawalList';
import './days.css';

export function daySubtitle(day: LiveDay) {
  const active = activeLiftCount(day);
  if (!active) return t.day.allCancelled;
  return `${t.day.liftsRunning(runningCount(day), active)} · ${t.day.riders(day.booked_rider_count)}`;
}

/** Minutes past midnight in the club's timezone, refreshed while the page is open. */
export function useClock(timeZone: string) {
  const [minutes, setMinutes] = useState(() => clockMinutes(timeZone));
  useEffect(() => {
    const timer = window.setInterval(() => setMinutes(clockMinutes(timeZone)), 30_000);
    return () => window.clearInterval(timer);
  }, [timeZone]);
  return minutes;
}

/** Every lift of the day on one card: seats, waitlist and whether it runs. */
export function LiftBoard({
  day,
  next,
  onOpen,
}: {
  day: LiveDay;
  next?: string;
  onOpen(time: string): void;
}) {
  return (
    <Card flush>
      <ul className="list">
        {day.lifts.map((lift) => {
          const state = liftState(lift);
          return (
            <li key={lift.time} className="row-item">
              <button
                type="button"
                className={cx('row', 'lift-row', state === 'cancelled' && 'lift-row-off')}
                onClick={() => onOpen(lift.time)}
              >
                <span className="lift-time tabular">{lift.time}</span>
                <span className="lift-body">
                  <span className="lift-line">
                    <span className={cx('lift-state', `tone-${liftTone[state]}`)}>
                      {liftStateLabel(state)}
                    </span>
                    {!lift.cancelled && lift.waiting_count > 0 && (
                      <Badge tone="warn">{t.lift.waitingBadge(lift.waiting_count)}</Badge>
                    )}
                    {next === lift.time && <Badge tone="accent">{t.lift.next}</Badge>}
                  </span>
                  <Meter
                    value={lift.cancelled ? 0 : lift.seat_count}
                    max={lift.capacity}
                    tone={state === 'below' ? 'warn' : 'ok'}
                  />
                </span>
                <span className="lift-count tabular">
                  <b>{lift.cancelled ? '—' : lift.seat_count}</b>
                  {!lift.cancelled && <span>/{lift.capacity}</span>}
                </span>
                <ChevronRight className="row-chevron" size={18} />
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Reported money against seats on running lifts; never presented as a debt. */
export function MoneyCard({
  day,
  attention,
  onOpen,
}: {
  day: LiveDay;
  attention: ReturnType<typeof dayAttention>;
  onOpen?: () => void;
}) {
  const { reported, due, missing } = dayMoney(day);
  const ridersKnown = !!day.riders;
  const body = (
    <>
      <span className="money-line">
        <span className="money-big tabular">{money(reported)}</span>
        <span className="money-of">{t.day.of(money(due))}</span>
        {onOpen && <ChevronRight className="row-chevron" size={18} />}
      </span>
      <Meter value={reported} max={Math.max(due, reported)} tone={missing > 0 ? 'warn' : 'ok'} />
      <span className="badges">
        {due === 0 ? (
          <Badge tone="muted">{t.day.notDueYet}</Badge>
        ) : missing > 0 ? (
          <Badge tone="warn" icon={<CircleAlert size={13} />}>
            {t.day.dueChip(money(missing))}
          </Badge>
        ) : (
          <Badge tone="ok" icon={<Check size={13} />}>
            {t.day.allPaid}
          </Badge>
        )}
        {ridersKnown && attention.unpaid.length > 0 && (
          <Badge icon={<Users size={13} />}>{t.day.unpaidChip(attention.unpaid.length)}</Badge>
        )}
        {ridersKnown && attention.cash.length > 0 && (
          <Badge icon={<Banknote size={13} />}>{t.day.cashChip(attention.cash.length)}</Badge>
        )}
      </span>
    </>
  );
  return (
    <Card className="money-card">
      {onOpen ? (
        <button type="button" className="money-card-body" onClick={onOpen}>
          {body}
        </button>
      ) : (
        <div className="money-card-body">{body}</div>
      )}
    </Card>
  );
}

/** Things only an admin can settle: unclear requests and late cancellations. */
export function AttentionList({
  attention,
  onRequests,
  onLift,
  timezone,
}: {
  timezone: string;
  attention: ReturnType<typeof dayAttention>;
  onRequests(): void;
  onLift(time: string): void;
}) {
  if (!attention.commands.length && !attention.lateExits.length) return null;
  return (
    <Section title={t.day.attention}>
      <Card flush>
        <List>
          {attention.commands.length > 0 && (
            <Row
              leading={<Clock3 size={19} />}
              tone="warn"
              title={t.day.requestsToCheck(attention.commands.length)}
              subtitle={t.day.requestsHint}
              onClick={onRequests}
            />
          )}
          {attention.lateExits.map((exit) => (
            <Row
              key={`${exit.telegram_user_id}-${exit.lift_time}`}
              leading={<CircleAlert size={19} />}
              tone="warn"
              title={`${exit.label} · ${exit.lift_time}`}
              subtitle={
                <>
                  {t.day.lateExit}
                  {exit.changed_at && (
                    <span className="withdrawal-time">
                      {withdrawalTime(exit.changed_at, timezone)}
                    </span>
                  )}
                </>
              }
              onClick={() => onLift(exit.lift_time)}
            />
          ))}
        </List>
      </Card>
    </Section>
  );
}
