import { describe, expect, it } from 'vitest';
import { moveBefore, orderPaymentLabel, proposeOrder } from './booking-order';
import type { BookingOrder } from './types';

describe('booking order', () => {
  it('moves a rider before another without disturbing everyone else', () => {
    expect(moveBefore([1, 2, 3, 4], 4, 2)).toEqual([1, 4, 2, 3]);
    expect(moveBefore([1, 2, 3], 1, null)).toEqual([2, 3, 1]);
    expect(moveBefore([1, 2, 3], 2, 2)).toEqual([1, 2, 3]);
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
        cash_on_site: i === 1,
      })),
    };
    const result = proposeOrder(order, [3, 1, 2]);
    expect(result.promoted).toEqual([3]);
    expect(result.demoted).toEqual([1]);
    expect(result.paid_demoted).toEqual([1]);
    expect(proposeOrder(order, [1, 3, 2]).promoted).toEqual([]);
  });
  it('keeps promises and money for other bookings distinct from paid seats', () => {
    const rider = {
      user_id: 1,
      label: 'Rider',
      waitlisted: false,
      paid: false,
      paid_gel: 0,
      cash_on_site: false,
    };
    expect(orderPaymentLabel(rider)).toBe('Оплата места не отмечена');
    expect(orderPaymentLabel({ ...rider, paid: true, paid_gel: 20 })).toBe('Оплата места отмечена');
    expect(orderPaymentLabel({ ...rider, cash_on_site: true })).toContain('ещё не получены');
    expect(orderPaymentLabel({ ...rider, paid_gel: 20 })).toContain('Оплата места не отмечена');
    expect(orderPaymentLabel({ ...rider, waitlisted: true, paid_gel: 20 })).toContain(
      'За день отмечено',
    );
  });
});
