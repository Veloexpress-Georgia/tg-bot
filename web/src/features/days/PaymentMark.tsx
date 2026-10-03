import { Banknote, Check, CircleAlert, CircleDashed, Hourglass, Wallet } from 'lucide-react';
import type { ReactNode } from 'react';
import { t } from '../../i18n';
import { cx, money } from '../../lib';

export type PaymentState = 'paid' | 'cash' | 'unpaid' | 'prepaid' | 'due' | 'none' | 'waitlist';

/** A payment status as an icon and a word or two. */
export function PaymentMark({
  state,
  amount = 0,
  position = 0,
}: {
  state: PaymentState;
  amount?: number;
  position?: number;
}) {
  const [icon, label, tone]: [ReactNode, string, string] = (() => {
    switch (state) {
      case 'paid':
        return [<Check size={14} />, t.pay.paid, 'ok'];
      case 'cash':
        return [<Banknote size={14} />, t.pay.cash, 'ok'];
      case 'unpaid':
        return [<CircleDashed size={14} />, t.pay.unpaid, 'muted'];
      case 'prepaid':
        return [<Wallet size={14} />, t.pay.prepaid(money(amount)), 'muted'];
      case 'due':
        return [<CircleAlert size={14} />, money(amount), 'warn'];
      case 'waitlist':
        return [<Hourglass size={14} />, `#${position}`, 'warn'];
      case 'none':
        return [null, t.pay.none, 'muted'];
    }
  })();
  return (
    <span className={cx('pay-mark', `tone-${tone}`)}>
      {icon}
      {label}
    </span>
  );
}
