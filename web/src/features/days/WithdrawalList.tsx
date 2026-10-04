import { Clock3 } from 'lucide-react';
import { t } from '../../i18n';
import { intlLocale } from '../../locale';
import type { BookingWithdrawal } from '../../types';
import { Badge, Card, Empty, List, Row, Section, Skeleton } from '../../ui';

export function withdrawalTime(value: string, timezone: string) {
  return new Date(value).toLocaleString(intlLocale(), {
    timeZone: timezone,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function WithdrawalList({
  entries,
  timezone,
  onLift,
}: {
  entries?: BookingWithdrawal[];
  timezone: string;
  onLift?(time: string): void;
}) {
  return (
    <Section title={t.withdrawals.title}>
      <p className="caption section-note">{t.withdrawals.timezone(timezone)}</p>
      {!entries ? (
        <Skeleton height={150} />
      ) : entries.length ? (
        <Card flush>
          <List>
            {entries.map((entry) => (
              <Row
                key={`${entry.event_id}-${entry.lift_time}`}
                title={`${entry.label}${onLift ? ` · ${entry.lift_time}` : ''}`}
                subtitle={
                  <time dateTime={entry.changed_at}>
                    {withdrawalTime(entry.changed_at, timezone)}
                  </time>
                }
                trailing={
                  <Badge tone={entry.after_deadline ? 'warn' : 'muted'}>
                    {entry.after_deadline ? t.withdrawals.after : t.withdrawals.before}
                  </Badge>
                }
                onClick={onLift ? () => onLift(entry.lift_time) : undefined}
              />
            ))}
          </List>
        </Card>
      ) : (
        <Card>
          <Empty icon={<Clock3 size={24} />} title={t.withdrawals.empty} />
        </Card>
      )}
    </Section>
  );
}
