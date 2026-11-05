import { describe, expect, it } from 'vitest';
import { moveBefore, owesNow, moveTo, proposeOrder, seatPayment } from './booking-order';
import type { BookingOrder } from './types';

describe('booking order', () => {
  it('moves a rider before another without disturbing everyone else', () => {
    expect(moveBefore([1, 2, 3, 4], 4, 2)).toEqual([1, 4, 2, 3]);
    expect(moveBefore([1, 2, 3], 1, null)).toEqual([2, 3, 1]);
    expect(moveBefore([1, 2, 3], 2, 2)).toEqual([1, 2, 3]);
  });
  it('moves across the seat boundary by position', () => {
    // Seats are the first three: a waiting rider takes the last seat, a seated one leads the line.
    expect(moveTo([1, 2, 3, 4, 5], 5, 2)).toEqual([1, 2, 5, 3, 4]);
    expect(moveTo([1, 2, 3, 4, 5], 2, 3)).toEqual([1, 3, 4, 2, 5]);
    expect(moveTo([1, 2, 3], 3, 0)).toEqual([3, 1, 2]);
    expect(moveTo([1, 2, 3], 1, 99)).toEqual([2, 3, 1]);
    expect(moveTo([1, 2, 3], 9, 0)).toEqual([1, 2, 3]);
  });
  it('counts reserved seats and exposes both sides of a seat change', () => {
    const order: BookingOrder = {
      digest: 'test',
      available_seats: 1,
      deadline_closed: true,
      previous_positions: {},
      riders: [1, 2, 3].map((uid, i) => ({
        user_id: uid,
        label: `Rider ${uid}`,
        waitlisted: i > 0,
        paid: i === 0,
        paid_gel: i === 0 ? 20 : 0,
        cash: false,
        state: i === 0 ? ('paid' as const) : ('waitlist' as const),
        due_gel: 0,
      })),
    };
    const result = proposeOrder(order, [3, 1, 2]);
    expect(result.promoted).toEqual([3]);
    expect(result.demoted).toEqual([1]);
    expect(result.paid_demoted).toEqual([1]);
    expect(proposeOrder(order, [1, 3, 2]).promoted).toEqual([]);
  });
  it('reads a seat from the ledger state, as the group board does', () => {
    const rider = {
      waitlisted: false,
      paid_gel: 0,
      cash: false,
      state: 'unpaid' as const,
      due_gel: 20,
    };
    expect(seatPayment(rider).state).toBe('unpaid');
    expect(seatPayment({ ...rider, state: 'paid', paid_gel: 20 }).state).toBe('paid');
    expect(seatPayment({ ...rider, state: 'paid', paid_gel: 20, cash: true }).state).toBe('cash');
    // A host who paid for themselves but not their guest still owes on this lift.
    expect(seatPayment({ ...rider, state: 'partial', paid_gel: 20 })).toEqual({
      state: 'due',
      amount: 20,
    });
    // Nothing is owed on a lift that has not filled, paid or not.
    expect(seatPayment({ ...rider, state: 'pending' }).state).toBe('none');
    expect(seatPayment({ ...rider, waitlisted: true, state: 'waitlist', paid_gel: 20 })).toEqual({
      state: 'prepaid',
      amount: 20,
    });
    expect(owesNow({ state: 'partial' })).toBe(true);
    expect(owesNow({ state: 'pending' })).toBe(false);
  });
});
