import { Bike, Check, ChevronDown, ExternalLink, Users, Wallet } from 'lucide-react';
import { Fragment, useState } from 'react';
import { useCommands } from '../../app/commands';
import { useMyDays } from '../../app/queries';
import { t } from '../../i18n';
import { money, plain } from '../../lib';
import { openTelegram } from '../../telegram';
import type { MyDays } from '../../types';
import {
  Badge,
  Button,
  Card,
  Empty,
  List,
  Notice,
  Page,
  PageHeader,
  Query,
  Row,
  Section,
  Segmented,
  Stepper,
} from '../../ui';
import { longDate, shortDate } from '../days/model';
import './rides.css';

export function MyRidesPage() {
  const myDays = useMyDays();
  return (
    <Page>
      <PageHeader title={t.rides.title} subtitle={t.rides.subtitle} />
      <Query query={myDays}>{(data) => <MyRides data={data} />}</Query>
    </Page>
  );
}

type RideDay = MyDays['days'][number];

function MyRides({ data }: { data: MyDays }) {
  const [selected, setSelected] = useState<string | null>(null);
  const first =
    data.days.find((day) => !day.past && day.booking?.rows.length) ??
    data.days.find((day) => !day.past) ??
    data.days[0];
  const day = data.days.find((item) => item.service_date === selected) ?? first;
  return (
    <>
      {data.days.length > 1 && (
        <Segmented
          className="ride-days"
          label={t.rides.day}
          value={day?.service_date ?? ''}
          onChange={setSelected}
          options={data.days.map((item) => ({
            value: item.service_date,
            label: shortDate(item.service_date),
          }))}
        />
      )}
      {day ? (
        <RideDayView key={day.service_date} day={day} data={data} />
      ) : (
        <Card>
          <Empty title={t.rides.noDays} text={t.rides.noDaysText} />
        </Card>
      )}
      <details className="disclosure ride-help card">
        <summary>
          <Bike size={18} />
          <span>{t.rides.helpTitle}</span>
          <ChevronDown className="disclosure-icon" size={18} />
        </summary>
        <div className="ride-help-body">
          <p className="caption">{t.rides.helpText}</p>
          <pre className="report">{plain(data.bank_details)}</pre>
          {data.polls_url && (
            <Button onClick={() => openTelegram(data.polls_url!)}>
              {t.rides.openGroup}
              <ExternalLink size={16} />
            </Button>
          )}
        </div>
      </details>
    </>
  );
}

function RideDayView({ day, data }: { day: RideDay; data: MyDays }) {
  const { act, busy } = useCommands();
  const [guestLift, setGuestLift] = useState<string | null>(null);
  const booking = day.booking;
  const date = day.service_date;
  const remaining = booking ? Math.max(booking.due_now_gel - booking.paid_gel, 0) : 0;
  // Both report the payment for the seats held now; cash is only handed over on site.
  const report = (method: 'cash' | 'transfer') =>
    act({ action: 'claim_payment', service_date: date, method });
  const cash = booking?.payment_method === 'cash';
  return (
    <Section
      title={longDate(date)}
      action={day.past ? <Badge tone="muted">{t.rides.past}</Badge> : undefined}
    >
      {booking?.rows.length ? (
        <div className="stack">
          <Card flush>
            <List>
              {booking.rows.map((row) => (
                <Fragment key={row.lift_time}>
                  <Row
                    leading={<span className="ride-time tabular">{row.lift_time}</span>}
                    title={
                      row.waitlist_position
                        ? t.rides.waitlist(row.waitlist_position)
                        : row.running
                          ? t.rides.seatHeld
                          : t.rides.waitingMinimum
                    }
                    tone={row.waitlist_position || !row.running ? 'warn' : 'ok'}
                    subtitle={row.guests ? t.rides.guests(row.guests) : undefined}
                    trailing={
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-expanded={guestLift === row.lift_time}
                        onClick={() =>
                          setGuestLift(guestLift === row.lift_time ? null : row.lift_time)
                        }
                      >
                        <Users size={15} />
                        {t.rides.guestsButton}
                      </Button>
                    }
                    className="ride-row"
                  />
                  {guestLift === row.lift_time && (
                    <Row
                      title={t.rides.guestSeats}
                      subtitle={t.rides.seatsLeft(row.seats_left)}
                      className="guest-row"
                      trailing={
                        <Stepper
                          value={row.guests}
                          canDecrement={!busy && !day.past && row.guests > 0}
                          canIncrement={!busy && !day.past && row.seats_left > 0}
                          onDecrement={() =>
                            act({
                              action: 'guest',
                              service_date: date,
                              lift_time: row.lift_time,
                              delta: -1,
                            })
                          }
                          onIncrement={() =>
                            act({
                              action: 'guest',
                              service_date: date,
                              lift_time: row.lift_time,
                              delta: 1,
                            })
                          }
                          decrementLabel={t.rides.removeGuest(row.lift_time)}
                          incrementLabel={t.rides.addGuest(row.lift_time)}
                        />
                      }
                    />
                  )}
                </Fragment>
              ))}
            </List>
          </Card>
          <dl className="facts">
            <div>
              <dt>{t.rides.dueNow}</dt>
              <dd className="tabular">{money(booking.due_now_gel)}</dd>
            </div>
            <div>
              <dt>{t.rides.marked}</dt>
              <dd className="tabular">{money(booking.paid_gel)}</dd>
            </div>
            <div>
              <dt>{t.rides.left}</dt>
              <dd className={remaining ? 'tone-warn tabular' : 'tabular'}>{money(remaining)}</dd>
            </div>
          </dl>
          {booking.pending_lift_times.length > 0 && (
            <p className="caption">{t.rides.pending(booking.pending_lift_times.join(', '))}</p>
          )}
          {remaining > 0 ? (
            <>
              <div className="ride-pay">
                <Button
                  variant="primary"
                  disabled={busy || day.past}
                  onClick={() => report('transfer')}
                >
                  <Check size={16} />
                  {t.rides.transferred}
                </Button>
                <Button disabled={busy || day.past} onClick={() => report('cash')}>
                  <Wallet size={16} />
                  {t.rides.payCash}
                </Button>
              </div>
              <p className="caption">{t.rides.cashNote}</p>
            </>
          ) : (
            <p className="ride-status tone-ok">
              <Check size={16} />
              {booking.paid_gel > 0
                ? cash
                  ? t.rides.paidCash
                  : t.rides.paymentMarked
                : t.rides.nothingDue}
            </p>
          )}
          <details className="disclosure card ride-details">
            <summary>
              <Wallet size={18} />
              <span>{t.rides.paymentDetails}</span>
              <ChevronDown className="disclosure-icon" size={18} />
            </summary>
            <div className="ride-help-body">
              <p className="caption">
                {t.rides.priceNote(money(booking.price_gel), money(booking.due_all_gel))}
              </p>
              <pre className="report">{plain(data.bank_details)}</pre>
              {remaining === 0 && booking.paid_gel > 0 && booking.payment_method !== 'mixed' && (
                <Button
                  className="btn-wrap"
                  disabled={busy || day.past}
                  onClick={() => report(cash ? 'transfer' : 'cash')}
                >
                  {cash ? t.rides.switchToTransfer : t.rides.switchToCash}
                </Button>
              )}
              {booking.paid_gel > 0 && (
                <Button
                  variant="ghost"
                  className="btn-wrap"
                  disabled={busy}
                  onClick={() => act({ action: 'undo_payment', service_date: date })}
                >
                  {t.rides.undoMark}
                </Button>
              )}
              <p className="caption">{t.rides.undoNote}</p>
            </div>
          </details>
        </div>
      ) : (
        <div className="stack">
          <p className="caption">{t.rides.noBooking}</p>
          <Card flush>
            <List>
              {day.lifts.map((lift) => (
                <Row
                  key={lift.time}
                  leading={<span className="ride-time tabular">{lift.time}</span>}
                  title={
                    lift.cancelled
                      ? t.lift.state.cancelled
                      : t.rides.free(Math.max(lift.capacity - lift.seats, 0))
                  }
                  tone={lift.cancelled ? 'muted' : undefined}
                />
              ))}
            </List>
          </Card>
          {data.polls_url && (
            <Button onClick={() => openTelegram(data.polls_url!)}>
              {t.rides.bookInPoll}
              <ExternalLink size={16} />
            </Button>
          )}
        </div>
      )}
    </Section>
  );
}
