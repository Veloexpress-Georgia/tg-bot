import { ChevronDown, Users } from 'lucide-react';
import { useState } from 'react';
import { t } from '../../i18n';
import { dateLabel, num } from '../../lib';
import type { Demand, DemandCounts, LiveDay } from '../../types';
import { Button, Card, Empty, List, Meter, Row } from '../../ui';

function demandLabel(row: DemandCounts) {
  if (row.waiting_total && row.waiting_total > 0) return t.demand.queueSaved;
  if (row.offered_lifts >= 3 && row.full_lifts / row.offered_lifts >= 0.5)
    return t.demand.oftenFull;
  if (row.offered_lifts >= 3 && row.not_run_lifts / row.offered_lifts >= 0.5)
    return t.demand.oftenNotRun;
  return row.offered_lifts < 3
    ? t.demand.fewObservations
    : t.demand.ranOf(row.ran_lifts, row.offered_lifts);
}

/**
 * Every offered lift counts, including the ones that did not run, so full
 * rates and empty seats are not flattered by leaving underfilled lifts out.
 */
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
  const summary = data.summary;
  const queues = liveDays
    .filter((day) => !day.past)
    .flatMap((day) =>
      day.lifts
        .filter((lift) => !lift.cancelled && lift.waiting_count > 0)
        .map((lift) => ({ day: day.service_date, lift })),
    );
  return (
    <Card className="analytics-card demand">
      <div className="card-head">
        <h2>{t.demand.title}</h2>
      </div>
      <p className="caption">{t.demand.intro}</p>
      <div className="demand-summary">
        <div>
          <span>{t.demand.lifts}</span>
          <strong className="tabular">{num(summary.offered_lifts)}</strong>
          <small>{t.demand.ranOf(summary.ran_lifts, summary.offered_lifts)}</small>
        </div>
        <div>
          <span>{t.demand.full}</span>
          <strong className="tabular">{num(summary.full_lifts)}</strong>
          <small>{t.demand.ofLifts(summary.offered_lifts)}</small>
        </div>
        <div>
          <span>{t.demand.waiting}</span>
          <strong className="tabular">
            {summary.waiting_total === null ? '—' : num(summary.waiting_total)}
          </strong>
          <small>
            {summary.queue_recorded_lifts
              ? t.demand.recorded(summary.queue_recorded_lifts, summary.offered_lifts)
              : summary.offered_lifts
                ? t.demand.noSaved
                : t.demand.noObservations}
          </small>
        </div>
      </div>
      {data.by_time.length ? (
        <div className="demand-times">
          {data.by_time.map((row) => {
            const open = expanded === row.time;
            return (
              <div className="demand-time" key={row.time}>
                <button
                  type="button"
                  className="demand-time-head"
                  aria-expanded={open}
                  onClick={() => {
                    setExpanded(open ? null : row.time);
                    setAllDays(false);
                  }}
                >
                  <strong className="tabular">{row.time}</strong>
                  <span>
                    <b>{demandLabel(row)}</b>
                    <small>
                      {t.demand.fullOf(row.full_lifts, row.offered_lifts)} ·{' '}
                      {t.demand.taken(num(row.occupancy_pct))}
                    </small>
                  </span>
                  <ChevronDown size={18} className="disclosure-icon" />
                </button>
                <Meter value={row.occupancy_pct} max={100} />
                {open && (
                  <div className="demand-detail">
                    <dl className="lines">
                      <div>
                        <dt>{t.demand.ran}</dt>
                        <dd className="tabular">
                          {row.ran_lifts} / {row.offered_lifts}
                        </dd>
                      </div>
                      <div>
                        <dt>{t.demand.freeAtClose}</dt>
                        <dd className="tabular">{row.free_seats}</dd>
                      </div>
                      <div>
                        <dt>{t.demand.queueAtClose}</dt>
                        <dd className="tabular">
                          {row.waiting_total === null
                            ? t.demand.noData
                            : t.demand.queueLifts(row.waiting_total, row.queued_lifts)}
                        </dd>
                      </div>
                    </dl>
                    {row.queue_unknown_lifts > 0 && (
                      <p className="caption">{t.demand.unknownQueues(row.queue_unknown_lifts)}</p>
                    )}
                    <div className="demand-days">
                      {(allDays ? row.days : row.days.slice(0, 6)).map((day) => (
                        <button type="button" key={day.date} onClick={() => openDay(day.date)}>
                          <span>
                            <strong>{dateLabel(day.date)}</strong>
                            <small>
                              {day.reconstructed
                                ? t.demand.reconstructed
                                : day.ran
                                  ? t.demand.didRun
                                  : t.demand.didNotRun}
                            </small>
                          </span>
                          <span className="tabular">
                            {day.seats} / {day.capacity}
                            {day.waiting_count !== null && (
                              <small>{t.demand.queueCount(day.waiting_count)}</small>
                            )}
                          </span>
                        </button>
                      ))}
                    </div>
                    {row.days.length > 6 && (
                      <Button variant="ghost" size="sm" onClick={() => setAllDays(!allDays)}>
                        {allDays ? t.demand.collapse : t.demand.showAll(row.days.length)}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <Empty title={t.demand.empty} />
      )}
      <p className="caption card-note">{t.demand.note}</p>
      <div className="demand-live">
        <h3>{t.demand.liveTitle}</h3>
        {liveStatus === 'error' ? (
          <p className="caption">{t.demand.liveError}</p>
        ) : liveStatus === 'pending' ? (
          <p className="caption">{t.common.loading}</p>
        ) : queues.length ? (
          <List>
            {queues.map(({ day, lift }) => (
              <Row
                key={`${day}-${lift.time}`}
                leading={<Users size={18} />}
                tone="warn"
                title={`${dateLabel(day)} · ${lift.time}`}
                subtitle={t.demand.liveRow(lift.seat_count, lift.capacity, lift.waiting_count)}
                onClick={() => openDay(day)}
              />
            ))}
          </List>
        ) : (
          <p className="caption">{t.demand.noLiveQueues}</p>
        )}
      </div>
    </Card>
  );
}
