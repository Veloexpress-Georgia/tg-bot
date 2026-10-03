import * as Menu from '@radix-ui/react-dropdown-menu';
import { ArrowDown, ArrowUp, MoreVertical, RotateCcw } from 'lucide-react';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useCommands } from '../../app/commands';
import { useBookingOrder } from '../../app/queries';
import { goBack, parseRoute, type Route } from '../../app/router';
import { moveBefore, moveTo, proposeOrder, seatPayment } from '../../booking-order';
import { t } from '../../i18n';
import { cx } from '../../lib';
import type { BookingOrder, Session } from '../../types';
import { Button, Page, PageHeader, Query } from '../../ui';
import { relativeDay, shortDate } from './model';
import { OrderConsequences } from './OrderConsequences';
import { PaymentMark } from './PaymentMark';
import './days.css';

type OrderRoute = Extract<Route, { name: 'order' }>;

export function OrderPage({ route, session }: { route: OrderRoute; session: Session }) {
  const order = useBookingOrder(route.date, route.time);
  return (
    <Page>
      <PageHeader
        eyebrow={`${relativeDay(route.date, session.today)} · ${shortDate(route.date)} · ${route.time}`}
        title={t.order.title}
      />
      <Query query={order}>
        {(data) => <OrderEditor key={data.digest} order={data} route={route} />}
      </Query>
    </Page>
  );
}

/**
 * The whole roster on one screen with the seat boundary visible. Arrows move
 * one step, the menu jumps across the boundary, and nothing is saved until the
 * server has previewed the consequences.
 */
function OrderEditor({ order, route }: { order: BookingOrder; route: OrderRoute }) {
  const { act, busy } = useCommands();
  const initial = useMemo(() => order.riders.map((rider) => rider.user_id), [order]);
  const [ids, setIds] = useState(initial);
  const [dragged, setDragged] = useState<number | null>(null);
  const riders = new Map(order.riders.map((rider) => [rider.user_id, rider]));
  const seats = order.available_seats;
  const changed = ids.some((uid, index) => initial[index] !== uid);
  const pointer = useMemo(() => window.matchMedia('(pointer: fine)').matches, []);
  const bar = useActionBar();
  // Back to the lift once saved, unless the admin has already moved on.
  const leave = () => {
    if (parseRoute(window.location.hash).name === 'order') goBack(route);
  };
  const save = () =>
    act(
      {
        action: 'booking_order',
        service_date: route.date,
        lift_time: route.time,
        ordered_user_ids: ids,
      },
      { onComplete: leave },
    );
  return (
    <>
      <p className="caption order-intro">{t.order.intro(seats)}</p>
      <ol className="order-list">
        {ids.map((uid, index) => {
          const rider = riders.get(uid)!;
          const previous = order.previous_positions[String(uid)];
          return (
            <Fragment key={uid}>
              {index === 0 && seats > 0 && (
                <li className="order-divider">{t.order.seatsHeader(seats)}</li>
              )}
              {index === seats && (
                <li className="order-divider order-divider-wait">{t.order.waitlistHeader}</li>
              )}
              <li
                className={cx(
                  'order-row',
                  index >= seats && 'order-row-wait',
                  dragged === uid && 'order-row-dragged',
                )}
                draggable={pointer && !busy}
                onDragStart={() => setDragged(uid)}
                onDragEnd={() => setDragged(null)}
                onDragOver={(event) => {
                  if (dragged !== null) event.preventDefault();
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (dragged !== null) setIds(moveBefore(ids, dragged, uid));
                  setDragged(null);
                }}
              >
                <span className="order-pos tabular">{index + 1}</span>
                <span className="order-name">
                  <span>{rider.label}</span>
                  <PaymentMark {...seatPayment(rider)} />
                </span>
                <span className="order-controls">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t.order.up(rider.label)}
                    disabled={busy || index === 0}
                    onClick={() => setIds(moveTo(ids, uid, index - 1))}
                  >
                    <ArrowUp size={18} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t.order.down(rider.label)}
                    disabled={busy || index === ids.length - 1}
                    onClick={() => setIds(moveTo(ids, uid, index + 1))}
                  >
                    <ArrowDown size={18} />
                  </Button>
                  <Menu.Root>
                    <Menu.Trigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t.order.more(rider.label)}
                        disabled={busy}
                      >
                        <MoreVertical size={18} />
                      </Button>
                    </Menu.Trigger>
                    <Menu.Portal>
                      <Menu.Content
                        className="menu"
                        align="end"
                        sideOffset={4}
                        collisionPadding={12}
                      >
                        {index > 0 && (
                          <Menu.Item
                            className="menu-item"
                            onSelect={() => setIds(moveTo(ids, uid, 0))}
                          >
                            {t.order.toTop}
                          </Menu.Item>
                        )}
                        {index >= seats && seats > 0 && (
                          <Menu.Item
                            className="menu-item"
                            onSelect={() => setIds(moveTo(ids, uid, seats - 1))}
                          >
                            {t.order.giveSeat}
                          </Menu.Item>
                        )}
                        {index < seats && ids.length > seats && (
                          <Menu.Item
                            className="menu-item"
                            onSelect={() => setIds(moveTo(ids, uid, seats))}
                          >
                            {t.order.toWaitlist}
                          </Menu.Item>
                        )}
                        {index < ids.length - 1 && (
                          <Menu.Item
                            className="menu-item"
                            onSelect={() => setIds(moveTo(ids, uid, ids.length - 1))}
                          >
                            {t.order.toEnd}
                          </Menu.Item>
                        )}
                        {previous !== undefined && (
                          <>
                            <Menu.Separator className="menu-separator" />
                            <Menu.Item
                              className="menu-item"
                              onSelect={() =>
                                act(
                                  {
                                    action: 'booking_order',
                                    service_date: route.date,
                                    lift_time: route.time,
                                    restore_user_id: uid,
                                  },
                                  { onComplete: leave },
                                )
                              }
                            >
                              <RotateCcw size={15} />
                              {t.order.restore(previous)}
                            </Menu.Item>
                          </>
                        )}
                      </Menu.Content>
                    </Menu.Portal>
                  </Menu.Root>
                </span>
              </li>
            </Fragment>
          );
        })}
      </ol>
      <p className="caption order-note">{pointer ? t.order.dragHint : t.order.tapHint}</p>
      <div className="action-bar" ref={bar}>
        <div className="action-bar-inner">
          {changed ? (
            <OrderConsequences proposal={proposeOrder(order, ids)} />
          ) : (
            <p className="caption">{t.order.noChanges}</p>
          )}
          <div className="action-bar-buttons">
            <Button disabled={busy || !changed} onClick={() => setIds(initial)}>
              <RotateCcw size={16} />
              {t.order.reset}
            </Button>
            <Button variant="primary" disabled={busy || !changed} onClick={save}>
              {t.order.review}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

/** Keeps floating notices and the page end clear of the fixed action bar. */
function useActionBar() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = document.documentElement;
    const element = ref.current;
    root.dataset.actionBar = 'true';
    const observer = new ResizeObserver(() =>
      root.style.setProperty('--action-bar', `${element?.offsetHeight ?? 0}px`),
    );
    if (element) observer.observe(element);
    return () => {
      observer.disconnect();
      delete root.dataset.actionBar;
      root.style.removeProperty('--action-bar');
    };
  }, []);
  return ref;
}
