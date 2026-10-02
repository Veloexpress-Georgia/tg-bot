import { useState } from 'react';
import { ArrowUpRight, ChevronDown, Users } from 'lucide-react';
import type { Demand, DemandCounts, LiveDay } from '../types';
import { dateLabel, number } from '../lib';
import { Empty } from './Shared';
import { Button } from './ui/button';

function demandLabel(row: DemandCounts) {
  if (row.waiting_total && row.waiting_total > 0) return 'Есть сохранённая очередь';
  if (row.offered_lifts >= 3 && row.full_lifts / row.offered_lifts >= 0.5)
    return 'Часто заполняется';
  if (row.offered_lifts >= 3 && row.not_run_lifts / row.offered_lifts >= 0.5)
    return 'Часто без выезда';
  return row.offered_lifts < 3
    ? 'Мало наблюдений'
    : `${row.ran_lifts} из ${row.offered_lifts} состоялись`;
}

export function DemandPanel({
  data,
  liveDays,
  liveStatus,
  openDay,
}: {
  data: Demand;
  liveDays: LiveDay[];
  liveStatus: 'pending' | 'error' | 'ready';
  openDay(day: string): void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [allDays, setAllDays] = useState(false);
  const currentQueues = liveDays
    .filter((day) => !day.past)
    .flatMap((day) =>
      day.lifts
        .filter((l) => !l.cancelled && l.waiting_count > 0)
        .map((l) => ({
          day: day.service_date,
          time: l.time,
          waiting: l.waiting_count,
          seats: l.seat_count,
          capacity: l.capacity,
        })),
    );
  const summary = data.summary;
  return (
    <section className="panel demand-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">РАСПИСАНИЕ И ВМЕСТИМОСТЬ</span>
          <h2>Спрос по времени выезда</h2>
        </div>
        <Users size={21} />
      </div>
      <p className="caption">
        Все предложенные выезды за выбранный период, включая те, что не состоялись. Отменённые
        выезды исключены.
      </p>
      <div className="demand-summary">
        <div>
          <span>Выезды</span>
          <strong>{number(summary.offered_lifts)}</strong>
          <small>
            {summary.ran_lifts} из {summary.offered_lifts} состоялись
          </small>
        </div>
        <div>
          <span>Полные</span>
          <strong>{number(summary.full_lifts)}</strong>
          <small>Из {summary.offered_lifts} выездов</small>
        </div>
        <div>
          <span>Мест в очереди</span>
          <strong>{summary.waiting_total === null ? '—' : number(summary.waiting_total)}</strong>
          <small>
            {summary.queue_recorded_lifts
              ? `${summary.queue_recorded_lifts} / ${summary.offered_lifts} выездов`
              : summary.offered_lifts
                ? 'Нет сохранённых данных'
                : 'Нет наблюдений'}
          </small>
        </div>
      </div>
      {data.by_time.length ? (
        <div className="demand-times">
          {data.by_time.map((row) => (
            <div className="demand-time" key={row.time}>
              <button
                className="demand-time-heading"
                aria-expanded={expanded === row.time}
                onClick={() => {
                  setExpanded(expanded === row.time ? null : row.time);
                  setAllDays(false);
                }}
              >
                <strong>{row.time}</strong>
                <span>
                  <b>{demandLabel(row)}</b>
                  <small>
                    {row.full_lifts} полных из {row.offered_lifts} · {number(row.occupancy_pct)}%
                    мест занято
                  </small>
                </span>
                <ChevronDown size={18} />
              </button>
              <div className="load-track">
                <div style={{ width: `${Math.min(row.occupancy_pct, 100)}%` }} />
              </div>
              {expanded === row.time && (
                <div className="demand-time-detail">
                  <dl>
                    <div>
                      <dt>Состоялись</dt>
                      <dd>
                        {row.ran_lifts} / {row.offered_lifts}
                      </dd>
                    </div>
                    <div>
                      <dt>Свободных мест при закрытии</dt>
                      <dd>{row.free_seats}</dd>
                    </div>
                    <div>
                      <dt>Очередь при закрытии</dt>
                      <dd>
                        {row.waiting_total === null
                          ? 'Нет данных'
                          : `${row.waiting_total} · ${row.queued_lifts} выездов`}
                      </dd>
                    </div>
                  </dl>
                  {row.queue_unknown_lifts > 0 && (
                    <p className="caption">
                      У {row.queue_unknown_lifts} выездов очередь не сохранялась или результат
                      восстановлен позже. Они не считаются нулевой очередью.
                    </p>
                  )}
                  <div className="demand-day-list">
                    {(allDays ? row.days : row.days.slice(0, 6)).map((day) => (
                      <button key={day.date} onClick={() => openDay(day.date)}>
                        <span>
                          <strong>{dateLabel(day.date)}</strong>
                          <small>
                            {day.reconstructed
                              ? 'Восстановлено позже'
                              : day.ran
                                ? 'Состоялся'
                                : 'Не состоялся'}
                          </small>
                        </span>
                        <span>
                          {day.seats} / {day.capacity}
                          {day.waiting_count !== null && <small>Очередь {day.waiting_count}</small>}
                        </span>
                        <ArrowUpRight size={16} />
                      </button>
                    ))}
                  </div>
                  {row.days.length > 6 && (
                    <Button variant="ghost" size="sm" onClick={() => setAllDays(!allDays)}>
                      {allDays ? 'Свернуть' : `Показать все ${row.days.length} дней`}
                    </Button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <Empty text="В этом периоде пока нет сохранённых выездов." />
      )}
      <p className="data-note">
        Очередь фиксируется при закрытии дня, а не на пике спроса. Считаются места по каждому
        выезду; одни бронирования могут попадать в очередь на нескольких выездах.
      </p>
      <div className="live-demand">
        <div className="workspace-heading">
          <h3>Очереди ближайших дней</h3>
          <span>Текущее состояние</span>
        </div>
        {liveStatus === 'error' ? (
          <p className="inline-notice">
            Не удалось загрузить ближайшие дни. Обнови данные, чтобы проверить текущие очереди.
          </p>
        ) : liveStatus === 'pending' ? (
          <p className="caption">Загружаем ближайшие дни…</p>
        ) : currentQueues.length ? (
          currentQueues.map((row) => (
            <button
              className="attention-item"
              key={`${row.day}-${row.time}`}
              onClick={() => openDay(row.day)}
            >
              <Users size={19} />
              <span>
                <strong>
                  {dateLabel(row.day)} · {row.time}
                </strong>
                <small>
                  {row.seats} / {row.capacity} мест · в очереди {row.waiting}
                </small>
              </span>
              <ArrowUpRight size={17} />
            </button>
          ))
        ) : (
          <p className="caption">В ближайших опубликованных выездах очередей нет.</p>
        )}
      </div>
    </section>
  );
}
