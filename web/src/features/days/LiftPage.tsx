import { Ban, ListOrdered, RotateCcw, UserPlus, Users, Wallet } from 'lucide-react';
import { useCommands } from '../../app/commands';
import { useDay } from '../../app/queries';
import { navigate, type Route } from '../../app/router';
import { t } from '../../i18n';
import type { Lift, LiftRider, LiveDay, Session } from '../../types';
import {
  Badge,
  Button,
  Card,
  Empty,
  List,
  Meter,
  Page,
  PageHeader,
  Query,
  Row,
  Section,
  Segmented,
  Skeleton,
  Stepper,
} from '../../ui';
import { liftState, liftStateLabel, liftTone, relativeDay, shortDate } from './model';
import { PaymentMark } from './PaymentMark';
import { WithdrawalList } from './WithdrawalList';
import './days.css';

type LiftRoute = Extract<Route, { name: 'lift' }>;
type RosterFilter = 'all' | 'unpaid' | 'waitlist';

export function LiftPage({ route, session }: { route: LiftRoute; session: Session }) {
  const detail = useDay(route.date);
  return (
    <Page>
      <Query query={detail}>
        {(day) => {
          const lift = day.historical ? undefined : day.lifts.find((l) => l.time === route.time);
          return day.historical || !lift ? (
            <Card>
              <Empty
                title={t.lift.notFound}
                action={
                  <Button onClick={() => navigate({ name: 'day', date: route.date, tab: 'lifts' })}>
                    {t.lift.openDay}
                  </Button>
                }
              />
            </Card>
          ) : (
            <LiftView day={day} lift={lift} route={route} session={session} />
          );
        }}
      </Query>
    </Page>
  );
}

function LiftView({
  day,
  lift,
  route,
  session,
}: {
  day: LiveDay;
  lift: Lift;
  route: LiftRoute;
  session: Session;
}) {
  const { act, busy } = useCommands();
  const state = liftState(lift);
  const riders = lift.riders;
  const seated = riders?.filter((rider) => !rider.waitlisted) ?? [];
  const waiting = riders?.filter((rider) => rider.waitlisted) ?? [];
  const unpaid = seated.filter((rider) => !rider.paid);
  const filter = (route.filter as RosterFilter | undefined) ?? 'all';
  const date = day.service_date;
  // Friends of the club who are not in Telegram: an admin holds their seat by hand.
  const adjust = (delta: 1 | -1) =>
    act({ action: 'manual', service_date: date, lift_time: lift.time, delta });
  return (
    <>
      <PageHeader
        eyebrow={`${relativeDay(date, session.today)} · ${shortDate(date)}`}
        title={lift.time}
        subtitle={t.lift.summary(lift.seat_count, lift.capacity, lift.waiting_count)}
      />
      <div className="lift-hero">
        <div className="badges">
          <Badge tone={liftTone[state]}>{liftStateLabel(state)}</Badge>
          {lift.funded && !lift.cancelled && (
            <Badge tone="ok" icon={<Wallet size={13} />}>
              {t.lift.funded}
            </Badge>
          )}
          {lift.manual_count > 0 && <Badge>{t.lift.offlineCount(lift.manual_count)}</Badge>}
          {lift.guest_count > 0 && <Badge>{t.lift.guestCount(lift.guest_count)}</Badge>}
        </div>
        <Meter
          value={lift.cancelled ? 0 : lift.seat_count}
          max={lift.capacity}
          tone={state === 'below' ? 'warn' : 'ok'}
          className="meter-lg"
        />
      </div>
      <Section title={t.lift.riders}>
        <Segmented
          chips
          className="roster-filter"
          label={t.lift.riders}
          value={filter}
          onChange={(next) =>
            navigate({ ...route, filter: next === 'all' ? undefined : next }, { replace: true })
          }
          options={[
            { value: 'all', label: t.lift.all, count: riders?.length },
            { value: 'unpaid', label: t.lift.unpaid, count: riders && unpaid.length },
            { value: 'waitlist', label: t.lift.waitlist, count: riders && waiting.length },
          ]}
        />
        {!riders ? (
          <Skeleton height={220} />
        ) : (
          <Roster
            seated={filter === 'waitlist' ? [] : filter === 'unpaid' ? unpaid : seated}
            waiting={filter === 'all' || filter === 'waitlist' ? waiting : []}
          />
        )}
      </Section>
      <WithdrawalList
        entries={day.withdrawals?.filter((entry) => entry.lift_time === lift.time)}
        timezone={session.timezone}
      />
      {!day.past && (
        <Section title={t.lift.manage}>
          <Card flush>
            <List>
              {!lift.cancelled && (riders?.length ?? 0) > 1 && (
                <Row
                  leading={<ListOrdered size={19} />}
                  title={t.lift.order}
                  subtitle={t.lift.orderHint}
                  onClick={() => navigate({ name: 'order', date, time: lift.time })}
                />
              )}
              <Row
                leading={<UserPlus size={19} />}
                title={t.lift.offlineSeats}
                subtitle={t.lift.offlineHint}
                trailing={
                  <Stepper
                    value={lift.manual_count}
                    canDecrement={!busy && lift.manual_count > 0}
                    canIncrement={!busy && !lift.cancelled && lift.seat_count < lift.capacity}
                    onDecrement={() => adjust(-1)}
                    onIncrement={() => adjust(1)}
                    decrementLabel={t.lift.removeOffline}
                    incrementLabel={t.lift.addOffline}
                  />
                }
              />
              <Row
                leading={lift.cancelled ? <RotateCcw size={19} /> : <Ban size={19} />}
                tone={lift.cancelled ? undefined : 'danger'}
                title={lift.cancelled ? t.lift.restore : t.lift.cancel}
                subtitle={lift.cancelled ? undefined : t.lift.cancelHint}
                disabled={busy}
                onClick={() =>
                  act({
                    action: lift.cancelled ? 'restore_lift' : 'cancel_lift',
                    service_date: date,
                    lift_time: lift.time,
                  })
                }
              />
            </List>
          </Card>
        </Section>
      )}
    </>
  );
}

function Roster({ seated, waiting }: { seated: LiftRider[]; waiting: LiftRider[] }) {
  if (!seated.length && !waiting.length)
    return (
      <Card>
        <Empty icon={<Users size={24} />} title={t.lift.emptyFilter} />
      </Card>
    );
  return (
    <Card flush>
      <List>
        {seated.map((rider) => (
          <RosterRow key={rider.telegram_user_id} rider={rider} />
        ))}
        {waiting.length > 0 && (
          <li className="list-divider">{t.lift.waitlistHeader(waiting.length)}</li>
        )}
        {waiting.map((rider, index) => (
          <RosterRow key={rider.telegram_user_id} rider={rider} position={index + 1} />
        ))}
      </List>
    </Card>
  );
}

function RosterRow({ rider, position }: { rider: LiftRider; position?: number }) {
  return (
    <Row
      title={rider.label}
      subtitle={rider.guests > 0 ? t.lift.withGuests(rider.guests) : undefined}
      trailing={
        rider.waitlisted ? (
          <PaymentMark state="waitlist" position={position} />
        ) : (
          <PaymentMark state={rider.paid ? (rider.cash ? 'cash' : 'paid') : 'unpaid'} />
        )
      }
    />
  );
}
