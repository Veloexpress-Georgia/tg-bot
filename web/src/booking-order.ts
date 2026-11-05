import type { BookingOrder, BookingOrderProposal, LiftRider } from './types';

/**
 * How one seat stands, read from the server's day ledger so every screen agrees
 * with the group board. Nothing is owed on a lift still short of the minimum.
 */
export function seatPayment(
  rider: Pick<LiftRider, 'state' | 'cash' | 'paid_gel' | 'due_gel' | 'waitlisted'>,
): { state: 'paid' | 'cash' | 'prepaid' | 'due' | 'unpaid' | 'none'; amount: number } {
  switch (rider.state) {
    case 'paid':
      return { state: rider.cash ? 'cash' : 'paid', amount: rider.paid_gel };
    case 'partial':
      return { state: 'due', amount: rider.due_gel };
    case 'unpaid':
      return { state: 'unpaid', amount: 0 };
    default:
      // Waiting for a seat or for the lift to fill: money already in is held, not owed.
      return rider.waitlisted && rider.paid_gel > 0
        ? { state: 'prepaid', amount: rider.paid_gel }
        : { state: 'none', amount: 0 };
  }
}

/** A seat that still owes money now: unpaid or only partly paid. */
export const owesNow = (rider: Pick<LiftRider, 'state'>) =>
  rider.state === 'unpaid' || rider.state === 'partial';

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
