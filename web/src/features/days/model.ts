import { t } from '../../i18n';
import { capitalize, dateLabel, daysBetween, liftMinutes } from '../../lib';
import type { Lift, LiveDay } from '../../types';
import type { Tone } from '../../ui';

/** Cash is reported like a transfer; it is only handed over on site. */
export const paysCash = (rider: { payment_method: string | null }) =>
  rider.payment_method === 'cash' || rider.payment_method === 'mixed';

export function dayAttention(day: LiveDay) {
  const riders = day.riders ?? [];
  return {
    unpaid: riders.filter((r) => r.due_now_gel > r.paid_gel),
    cash: riders.filter((r) => r.paid_gel > 0 && paysCash(r)),
    queues: day.lifts.filter((l) => !l.cancelled && l.waiting_count > 0),
    underfilled: day.lifts.filter((l) => !l.cancelled && !l.running),
    lateExits: day.late_exits,
    commands: (day.commands ?? []).filter((c) =>
      ['pending', 'running', 'review', 'failed'].includes(c.status),
    ),
  };
}

export type LiftState = 'cancelled' | 'below' | 'waitlist' | 'full' | 'running';
type LiftCounts = Pick<Lift, 'cancelled' | 'running' | 'waiting_count' | 'seat_count' | 'capacity'>;

/** Seats and the waitlist stay separate numbers: 10/10 · +4, never 14/10. */
export function liftState(lift: LiftCounts): LiftState {
  if (lift.cancelled) return 'cancelled';
  if (!lift.running) return 'below';
  if (lift.waiting_count > 0) return 'waitlist';
  if (lift.seat_count >= lift.capacity) return 'full';
  return 'running';
}
export const liftTone: Record<LiftState, Tone> = {
  cancelled: 'muted',
  below: 'warn',
  waitlist: 'warn',
  full: 'ok',
  running: 'ok',
};
export const liftStateLabel = (state: LiftState) => t.lift.state[state];

/**
 * The day an admin is most likely working on: today, else the next one ahead,
 * else the latest day still open for late bookkeeping.
 */
export function focusDay(days: LiveDay[], today: string): LiveDay | undefined {
  return (
    days.find((day) => day.service_date === today) ??
    days.find((day) => day.service_date > today) ??
    [...days].reverse().find((day) => day.service_date < today)
  );
}

/** On the day itself, the first lift that has not left yet by the clock. */
export function nextLiftTime(day: LiveDay, today: string, minutesNow: number) {
  if (day.service_date !== today) return undefined;
  return day.lifts.find((lift) => !lift.cancelled && liftMinutes(lift.time) >= minutesNow)?.time;
}

export const runningCount = (day: LiveDay) =>
  day.lifts.filter((lift) => !lift.cancelled && lift.running).length;
export const activeLiftCount = (day: LiveDay) => day.lifts.filter((lift) => !lift.cancelled).length;

/** Reported money against the price of seats on lifts that reached the minimum. */
export function dayMoney(day: LiveDay) {
  return {
    reported: day.expected_gel,
    due: day.owed_gel,
    missing: Math.max(day.owed_gel - day.expected_gel, 0),
  };
}

export function relativeDay(date: string, today: string) {
  const diff = daysBetween(today, date);
  if (diff === 0) return t.days.today;
  if (diff === 1) return t.days.tomorrow;
  if (diff === -1) return t.days.yesterday;
  return diff > 0 ? t.days.inDays(diff) : t.days.daysAgo(-diff);
}

export const longDate = (date: string, withYear = false) =>
  capitalize(
    dateLabel(date, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      ...(withYear ? { year: 'numeric' as const } : {}),
    }),
  );
export const shortDate = (date: string) =>
  dateLabel(date, { weekday: 'short', day: 'numeric', month: 'short' });
