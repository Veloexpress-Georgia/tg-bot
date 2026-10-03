import { seatPayment } from '../../booking-order';
import { t } from '../../i18n';
import { cx } from '../../lib';
import type { BookingOrderProposal } from '../../types';
import { Notice } from '../../ui';
import { PaymentMark } from './PaymentMark';

export function OrderConsequences({
  proposal,
  detailed = false,
}: {
  proposal: BookingOrderProposal;
  detailed?: boolean;
}) {
  const riders = new Map(proposal.riders.map((rider) => [rider.user_id, rider]));
  const names = (ids: number[]) => ids.map((uid) => riders.get(uid)?.label ?? uid).join(', ');
  return (
    <div className="order-effects" aria-live="polite">
      {proposal.paid_demoted.length > 0 && (
        <Notice tone="warn">
          <strong>{t.order.paidDemoted(names(proposal.paid_demoted))}</strong>{' '}
          {t.order.paidDemotedNote}
        </Notice>
      )}
      {proposal.promoted.length > 0 && (
        <p>
          {t.order.promoted} <strong>{names(proposal.promoted)}</strong>
        </p>
      )}
      {proposal.demoted.length > 0 && (
        <p className="tone-warn">
          {t.order.demoted} <strong>{names(proposal.demoted)}</strong>
        </p>
      )}
      {!proposal.promoted.length && !proposal.demoted.length && <p>{t.order.sameSeats}</p>}
      {detailed && (
        <ol className="order-preview">
          {proposal.ordered_user_ids.map((uid, index) => {
            const rider = riders.get(uid);
            const seat = index < proposal.available_seats;
            return (
              <li key={uid}>
                <span className="order-preview-pos tabular">{index + 1}</span>
                <span className="order-preview-name">
                  {rider?.label ?? uid}
                  {rider && <PaymentMark {...seatPayment(rider)} />}
                </span>
                <span className={cx('badge', seat ? 'badge-ok' : 'badge-warn')}>
                  {seat ? t.order.seat : t.order.waitlist}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {detailed && <p className="caption">{t.order.paymentsBefore}</p>}
      {detailed && proposal.deadline_closed && <p className="caption">{t.order.deadlineNote}</p>}
    </div>
  );
}
