import { ArrowRight, CalendarPlus, ChevronRight } from 'lucide-react';
import { useLiveDays } from '../../app/queries';
import { navigate } from '../../app/router';
import { t } from '../../i18n';
import { cx, money } from '../../lib';
import type { LiveDay, Session } from '../../types';
import { Button, Card, Empty, Page, PageHeader, Query } from '../../ui';
import { daySubtitle } from './DaySummary';
import { dayMoney, liftState, liftTone, longDate, relativeDay } from './model';
import './days.css';

export function DaysPage({ session }: { session: Session }) {
  const days = useLiveDays();
  return (
    <Page>
      <PageHeader title={t.days.title} subtitle={t.days.subtitle} />
      <Query query={days}>
        {(list) =>
          list.length ? (
            <div className="day-cards">
              {list.map((day) => (
                <DayCard key={day.service_date} day={day} today={session.today} />
              ))}
            </div>
          ) : (
            <Card>
              <Empty
                icon={<CalendarPlus size={24} />}
                title={t.days.emptyTitle}
                text={t.days.emptyText}
                action={
                  <Button variant="primary" onClick={() => navigate({ name: 'planning' })}>
                    {t.home.openPlanning}
                  </Button>
                }
              />
            </Card>
          )
        }
      </Query>
      <Button
        variant="ghost"
        block
        className="days-history"
        onClick={() => navigate({ name: 'analytics' })}
      >
        {t.days.history}
        <ArrowRight size={16} />
      </Button>
    </Page>
  );
}

function DayCard({ day, today }: { day: LiveDay; today: string }) {
  const { reported, due } = dayMoney(day);
  return (
    <button
      type="button"
      className="card day-card"
      onClick={() => navigate({ name: 'day', date: day.service_date, tab: 'lifts' })}
    >
      <span className="day-card-head">
        <span>
          <span className="eyebrow">{relativeDay(day.service_date, today)}</span>
          <strong>{longDate(day.service_date)}</strong>
        </span>
        <ChevronRight className="row-chevron" size={20} />
      </span>
      <span className="day-card-lifts">
        {day.lifts.map((lift) => {
          const state = liftState(lift);
          return (
            <span key={lift.time} className={cx('mini-lift', `mini-lift-${liftTone[state]}`)}>
              <b className="tabular">{lift.time}</b>
              <span className="tabular">
                {lift.cancelled ? '—' : `${lift.seat_count}/${lift.capacity}`}
                {!lift.cancelled && lift.waiting_count > 0 && ` +${lift.waiting_count}`}
              </span>
            </span>
          );
        })}
      </span>
      <span className="day-card-foot">
        <span>{daySubtitle(day)}</span>
        <span className="tabular">{t.day.reportedOf(money(reported), money(due))}</span>
      </span>
    </button>
  );
}
