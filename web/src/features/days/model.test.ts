import { describe, expect, it } from 'vitest';
import { demoDays } from '../../demo';
import type { LiveDay } from '../../types';
import { dayAttention, dayMoney, focusDay, liftState, nextLiftTime, relativeDay } from './model';

const day = () => structuredClone(demoDays[0]);
const summary = (service_date: string, past = false) => ({ ...day(), service_date, past });

describe('day attention', () => {
  it('counts cash reported for on site as paid and lists who to collect from', () => {
    const value = day();
    const rider = value.riders!.find((r) => r.due_now_gel > 0)!;
    rider.paid_gel = rider.due_now_gel;
    rider.payment_method = 'cash';
    const result = dayAttention(value);
    expect(result.unpaid.some((r) => r.user_id === rider.user_id)).toBe(false);
    expect(result.cash.some((r) => r.user_id === rider.user_id)).toBe(true);
    rider.paid_gel = 0;
    rider.payment_method = null;
    const unpaid = dayAttention(value);
    expect(unpaid.unpaid.some((r) => r.user_id === rider.user_id)).toBe(true);
    expect(unpaid.cash.some((r) => r.user_id === rider.user_id)).toBe(false);
  });
  it('keeps queues, underfilled lifts and unreported amounts separate', () => {
    const result = dayAttention(day());
    expect(result.queues.map((l) => l.time)).toEqual(['11:45', '13:30']);
    expect(result.underfilled.map((l) => l.time)).toEqual(['8:30']);
    expect(result.unpaid.every((r) => r.due_now_gel > r.paid_gel)).toBe(true);
    expect(result.commands.map((c) => c.status)).toEqual(['review']);
  });
  it('does not flag cancelled lifts or covered payments', () => {
    const value = day();
    value.lifts.forEach((l) => {
      l.cancelled = true;
    });
    value.riders?.forEach((r) => {
      r.paid_gel = r.due_now_gel + 20;
    });
    value.commands = [];
    const result = dayAttention(value);
    expect(result.queues).toEqual([]);
    expect(result.underfilled).toEqual([]);
    expect(result.unpaid).toEqual([]);
    expect(result.commands).toEqual([]);
  });
  it('waits for rider details instead of reporting nobody as unpaid', () => {
    const value: LiveDay = { ...day(), riders: undefined };
    expect(dayAttention(value).unpaid).toEqual([]);
  });
});

describe('lift state', () => {
  const lift = { cancelled: false, running: true, waiting_count: 0, seat_count: 7, capacity: 10 };
  it('keeps seats and the waitlist apart', () => {
    expect(liftState(lift)).toBe('running');
    expect(liftState({ ...lift, seat_count: 10 })).toBe('full');
    expect(liftState({ ...lift, seat_count: 10, waiting_count: 4 })).toBe('waitlist');
    expect(liftState({ ...lift, running: false, seat_count: 2 })).toBe('below');
    expect(liftState({ ...lift, cancelled: true })).toBe('cancelled');
  });
});

describe('which day the cabinet opens on', () => {
  it('prefers today, then the next day, then the latest open past day', () => {
    const past = summary('2026-10-03', true);
    const today = summary('2026-10-04');
    const next = summary('2026-10-10');
    expect(focusDay([past, today, next], '2026-10-04')?.service_date).toBe('2026-10-04');
    expect(focusDay([past, next], '2026-10-04')?.service_date).toBe('2026-10-10');
    expect(focusDay([past], '2026-10-04')?.service_date).toBe('2026-10-03');
    expect(focusDay([], '2026-10-04')).toBeUndefined();
  });
  it('marks the next lift only on the day itself', () => {
    const today = day();
    expect(nextLiftTime(today, '2026-10-04', 9 * 60)).toBe('10:00');
    expect(nextLiftTime(today, '2026-10-04', 16 * 60)).toBeUndefined();
    expect(nextLiftTime(today, '2026-10-03', 9 * 60)).toBeUndefined();
  });
  it('labels days relative to today', () => {
    expect(relativeDay('2026-10-04', '2026-10-04')).toBe('Today');
    expect(relativeDay('2026-10-05', '2026-10-04')).toBe('Tomorrow');
    expect(relativeDay('2026-10-10', '2026-10-04')).toBe('In 6 days');
    expect(relativeDay('2026-10-02', '2026-10-04')).toBe('2 days ago');
  });
});

describe('day money', () => {
  it('compares reported money with seats on running lifts', () => {
    const value = { ...day(), expected_gel: 480, owed_gel: 600 };
    expect(dayMoney(value)).toEqual({ reported: 480, due: 600, missing: 120 });
    expect(dayMoney({ ...value, expected_gel: 700 }).missing).toBe(0);
  });
});
