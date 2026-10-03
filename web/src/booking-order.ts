import type { BookingOrder, BookingOrderProposal } from './types';

/** How the rider's seat stands: paid by transfer or in cash, money ahead while waiting, or nothing. */
export function seatPayment(rider: BookingOrder['riders'][number]): {
  state: 'paid' | 'cash' | 'prepaid' | 'unpaid';
  amount: number;
} {
  if (rider.paid) return { state: rider.cash ? 'cash' : 'paid', amount: rider.paid_gel };
  if (rider.waitlisted && rider.paid_gel > 0) return { state: 'prepaid', amount: rider.paid_gel };
  return { state: 'unpaid', amount: 0 };
}

export function moveBefore(ids: number[], source: number, target: number | null): number[] {
  if (!ids.includes(source) || source === target || (target !== null && !ids.includes(target)))
    return ids;
  const next = ids.filter((id) => id !== source);
  next.splice(target === null ? next.length : next.indexOf(target), 0, source);
  return next;
}

/** Moves a rider to a zero-based position in the list. */
export function moveTo(ids: number[], source: number, index: number): number[] {
  if (!ids.includes(source)) return ids;
  const next = ids.filter((id) => id !== source);
  next.splice(Math.max(0, Math.min(index, next.length)), 0, source);
  return next;
}

export function proposeOrder(order: BookingOrder, ids: number[]): BookingOrderProposal {
  const before = new Set(order.riders.slice(0, order.available_seats).map((r) => r.user_id));
  const after = new Set(ids.slice(0, order.available_seats));
  return {
    ...order,
    ordered_user_ids: ids,
    promoted: ids.filter((uid) => after.has(uid) && !before.has(uid)),
    demoted: order.riders.map((r) => r.user_id).filter((uid) => before.has(uid) && !after.has(uid)),
    paid_demoted: order.riders
      .filter((r) => r.paid && before.has(r.user_id) && !after.has(r.user_id))
      .map((r) => r.user_id),
  };
}
