import type { BookingOrder, BookingOrderProposal } from './types';
import { money } from './lib';

export function orderPaymentLabel(rider: BookingOrder['riders'][number]): string {
  if (rider.paid) return 'Оплата места отмечена';
  const received = rider.paid_gel > 0 ? ` · за день отмечено ${money(rider.paid_gel)}` : '';
  if (rider.cash_on_site) return `Наличные на месте · ещё не получены${received}`;
  if (rider.waitlisted && rider.paid_gel > 0) return `За день отмечено ${money(rider.paid_gel)}`;
  return `Оплата места не отмечена${received}`;
}

export function moveBefore(ids: number[], source: number, target: number | null): number[] {
  if (!ids.includes(source) || source === target || (target !== null && !ids.includes(target)))
    return ids;
  const next = ids.filter((id) => id !== source);
  next.splice(target === null ? next.length : next.indexOf(target), 0, source);
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
