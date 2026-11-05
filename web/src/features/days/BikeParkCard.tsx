import { Bike, ExternalLink, Users } from 'lucide-react';
import { useCommands } from '../../app/commands';
import { t } from '../../i18n';
import { money } from '../../lib';
import { openTelegram } from '../../telegram';
import type { BikePark } from '../../types';
import { Badge, Button, Card, List, Notice, Row, Section } from '../../ui';

export function BikeParkCard({
  park,
  admin = false,
}: {
  park: BikePark | (Omit<BikePark, 'riders'> & { rider_count: number; attending: boolean });
  admin?: boolean;
}) {
  const { act, busy } = useCommands();
  const riders = 'riders' in park ? park.riders : undefined;
  const count = riders?.length ?? ('rider_count' in park ? park.rider_count : 0);
  return (
    <Section
      title={t.park.title}
      action={
        <Badge icon={<Bike size={14} />}>
          {park.start_time} · {t.park.location}
        </Badge>
      }
    >
      <Card className="stack">
        <div className="badges">
          <Badge icon={<Users size={14} />}>{t.park.signedUp(count)}</Badge>
          <Badge>{t.park.perLift(money(park.price_gel))}</Badge>
          {'attending' in park && park.attending && <Badge tone="ok">{t.park.attending}</Badge>}
        </div>
        <p className="caption">{t.park.note}</p>
        {park.started && <Notice>{t.park.started}</Notice>}
        {park.status !== 'posted' && <Notice>{t.park.uncertain}</Notice>}
        {riders && riders.length > 0 && (
          <details className="disclosure">
            <summary>{t.park.signedUp(count)}</summary>
            <List>
              {riders.map((rider) => (
                <Row key={rider.telegram_user_id} title={rider.label} />
              ))}
            </List>
          </details>
        )}
        {park.poll_url && (
          <Button onClick={() => openTelegram(park.poll_url!)}>
            <ExternalLink size={16} />
            {t.park.open}
          </Button>
        )}
        {admin && !park.started && (
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => act({ action: 'cancel_bike_park', service_date: park.service_date })}
          >
            {t.park.cancel}
          </Button>
        )}
      </Card>
    </Section>
  );
}
