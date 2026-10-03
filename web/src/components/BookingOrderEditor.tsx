import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, GripVertical, RotateCcw } from 'lucide-react';
import { request } from '../api';
import { moveBefore, orderPaymentLabel, proposeOrder } from '../booking-order';
import type { BookingOrder, BookingOrderProposal } from '../types';
import type { Act } from './Operations';
import { Button } from './ui/button';
import { Select } from './ui/select';

export function BookingOrderEditor({
  day,
  time,
  act,
  busy,
  onClose,
}: {
  day: string;
  time: string;
  act: Act;
  busy: boolean;
  onClose(): void;
}) {
  const order = useQuery({
    queryKey: ['booking-order', day, time],
    queryFn: () =>
      request<BookingOrder>(`/api/admin/days/${day}/lifts/${encodeURIComponent(time)}/order`),
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
  if (order.isPending) return <div className="skeleton" aria-label="Загружаем порядок записи" />;
  if (order.isError)
    return (
      <div className="error-state">
        <p>{order.error.message}</p>
        <Button variant="secondary" onClick={() => order.refetch()}>
          Обновить
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>
    );
  return (
    <OrderDraft
      key={order.data.digest}
      order={order.data}
      day={day}
      time={time}
      act={act}
      busy={busy}
      onClose={onClose}
    />
  );
}

function OrderDraft({
  order,
  day,
  time,
  act,
  busy,
  onClose,
}: {
  order: BookingOrder;
  day: string;
  time: string;
  act: Act;
  busy: boolean;
  onClose(): void;
}) {
  const initial = order.riders.map((r) => r.user_id);
  const [ids, setIds] = useState(initial);
  const [source, setSource] = useState(initial[0] ?? 0);
  const [dragged, setDragged] = useState<number | null>(null);
  const changed = ids.some((uid, i) => initial[i] !== uid);
  const labels = new Map(order.riders.map((r) => [r.user_id, r.label]));
  const riders = new Map(order.riders.map((r) => [r.user_id, r]));
  const proposal = proposeOrder(order, ids);
  const move = (uid: number, target: number | null) =>
    setIds((current) => moveBefore(current, uid, target));
  return (
    <section className="booking-order-editor" aria-label="Изменить порядок записи">
      <div className="workspace-heading">
        <h3>Порядок записи · {time}</h3>
      </div>
      <p className="caption">
        Перетащи участника или используй стрелки. Внешние места и гости сохраняют свои места.
      </p>
      <p className="caption">Отметки оплаты показаны до перестановки.</p>
      <div className="order-place-controls">
        <Select<number>
          label="Кого переместить"
          value={source}
          onValueChange={setSource}
          options={ids.map((uid) => ({
            value: uid,
            label: `${labels.get(uid)!} · ${orderPaymentLabel(riders.get(uid)!)}`,
          }))}
          disabled={busy}
        />
        <Select<number>
          label="Поставить перед участником"
          value={-1}
          onValueChange={(target) => {
            if (target !== -1) move(source, target || null);
          }}
          options={[
            { value: -1, label: 'Поставить перед…' },
            ...ids
              .filter((uid) => uid !== source)
              .map((uid) => ({
                value: uid,
                label: `${labels.get(uid)!} · ${orderPaymentLabel(riders.get(uid)!)}`,
              })),
            { value: 0, label: 'В конец списка' },
          ]}
          disabled={busy}
        />
      </div>
      {order.previous_positions[String(source)] !== undefined && (
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            act({
              action: 'booking_order',
              service_date: day,
              lift_time: time,
              restore_user_id: source,
            });
            onClose();
          }}
        >
          <RotateCcw size={15} />
          Вернуть прежнюю позицию · {order.previous_positions[String(source)]}
        </Button>
      )}
      <ol className="booking-order-list">
        {ids.map((uid, i) => (
          <li key={uid}>
            {(i === 0 || i === order.available_seats) && (
              <div className="order-divider">{i < order.available_seats ? 'Места' : 'Очередь'}</div>
            )}
            <div
              className={`order-rider ${i >= order.available_seats ? 'order-waiting' : ''}`}
              draggable={!busy}
              onDragStart={() => setDragged(uid)}
              onDragEnd={() => setDragged(null)}
              onDragOver={(e) => {
                if (!busy) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (!busy && dragged !== null) move(dragged, uid);
                setDragged(null);
              }}
            >
              <GripVertical size={16} aria-hidden="true" />
              <span className="order-position">{i + 1}</span>
              <span className="order-label">
                {labels.get(uid)}
                <small
                  className={`order-payment ${riders.get(uid)!.paid ? 'paid-text' : 'muted-text'}`}
                >
                  {orderPaymentLabel(riders.get(uid)!)}
                </small>
              </span>
              <Button
                variant="ghost"
                size="icon"
                disabled={busy || i === 0}
                aria-label={`Поднять ${labels.get(uid)}`}
                onClick={() => move(uid, ids[i - 1])}
              >
                <ArrowUp size={16} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                disabled={busy || i === ids.length - 1}
                aria-label={`Опустить ${labels.get(uid)}`}
                onClick={() => move(uid, ids[i + 2] ?? null)}
              >
                <ArrowDown size={16} />
              </Button>
            </div>
          </li>
        ))}
      </ol>
      {changed && <BookingOrderConsequences proposal={proposal} compact />}
      <div className="dialog-actions">
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Отмена
        </Button>
        <Button
          disabled={busy || !changed}
          onClick={() => {
            act({
              action: 'booking_order',
              service_date: day,
              lift_time: time,
              ordered_user_ids: ids,
            });
            onClose();
          }}
        >
          Проверить и сохранить
        </Button>
      </div>
    </section>
  );
}

export function BookingOrderConsequences({
  proposal,
  compact = false,
}: {
  proposal: BookingOrderProposal;
  compact?: boolean;
}) {
  const label = (uid: number) =>
    proposal.riders.find((r) => r.user_id === uid)?.label ?? String(uid);
  return (
    <div className="order-consequences" aria-live="polite">
      {proposal.paid_demoted.length > 0 && (
        <p className="warning-text">
          <strong>
            Оплаченные брони перейдут в очередь: {proposal.paid_demoted.map(label).join(', ')}
          </strong>{' '}
          Оплаты не переносятся и возврат не оформляется.
        </p>
      )}
      {proposal.promoted.length > 0 && (
        <p>
          Получат место: <strong>{proposal.promoted.map(label).join(', ')}</strong>
        </p>
      )}
      {proposal.demoted.length > 0 && (
        <p className="warning-text">
          Перейдут в очередь: <strong>{proposal.demoted.map(label).join(', ')}</strong>
        </p>
      )}
      {!proposal.promoted.length && !proposal.demoted.length && (
        <p>Места останутся у текущих участников. Изменится порядок на следующие свободные места.</p>
      )}
      {!compact && (
        <ol className="order-preview-list">
          {proposal.ordered_user_ids.map((uid, i) => (
            <li key={uid}>
              <div className="order-preview-rider">
                <div>
                  {label(uid)}
                  <small
                    className={`order-payment ${proposal.riders.find((r) => r.user_id === uid)!.paid ? 'paid-text' : 'muted-text'}`}
                  >
                    {orderPaymentLabel(proposal.riders.find((r) => r.user_id === uid)!)}
                  </small>
                </div>
                <span>{i < proposal.available_seats ? 'Место' : 'Очередь'}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
      {!compact && <p className="caption">Отметки оплаты показаны до перестановки.</p>}
      {proposal.deadline_closed && (
        <p className="caption">
          Состав на дедлайн сохранится. Оплаты между участниками не переносятся; возвраты эта
          операция не оформляет.
        </p>
      )}
    </div>
  );
}
