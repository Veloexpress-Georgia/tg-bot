import { ArrowRight, CalendarPlus } from 'lucide-react';
import { useAnalytics, useDay, useLiveDays, usePlanning } from '../../app/queries';
import { navigate } from '../../app/router';
import { t } from '../../i18n';
import { capitalize, dateLabel, money, num, periodRange } from '../../lib';
import type { LiveDay, Session } from '../../types';
import { Button, Card, Empty, List, Page, PageHeader, Query, Row, Section } from '../../ui';
import { AttentionList, LiftBoard, MoneyCard, daySubtitle, useClock } from '../days/DaySummary';
import {
  dayAttention,
  focusDay,
  longDate,
  nextLiftTime,
  relativeDay,
  shortDate,
} from '../days/model';
import './home.css';

/**
 * Home answers "what is happening with the lifts" before anything else:
 * today's day if there is one, otherwise the next published day.
 */
export function HomePage({ session }: { session: Session }) {
  const days = useLiveDays();
  return (
    <Page>
      <Query query={days}>
        {(list) => {
          const day = focusDay(list, session.today);
          return day ? (
            <FocusDay
              key={day.service_date}
              day={day}
              others={list.filter((other) => other !== day)}
              session={session}
            />
          ) : (
            <NoDays />
          );
        }}
      </Query>
      <MonthSummary session={session} />
    </Page>
  );
}

function FocusDay({
  day: summary,
  others,
  session,
}: {
  day: LiveDay;
  others: LiveDay[];
  session: Session;
}) {
  const date = summary.service_date;
  const detail = useDay(date);
  const day = detail.data && !detail.data.historical ? detail.data : summary;
  const attention = dayAttention(day);
  const minutes = useClock(session.timezone);
  return (
    <>
      <PageHeader
        eyebrow={relativeDay(date, session.today)}
        title={longDate(date)}
        subtitle={daySubtitle(day)}
      />
      <Section
        title={t.home.lifts}
        action={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate({ name: 'day', date, tab: 'lifts' })}
          >
            {t.home.openDay}
            <ArrowRight size={16} />
          </Button>
        }
      >
        <LiftBoard
          day={day}
          next={nextLiftTime(day, session.today, minutes)}
          onOpen={(time) => navigate({ name: 'lift', date, time })}
        />
      </Section>
      <Section title={t.home.payments}>
        <MoneyCard
          day={day}
          attention={attention}
          onOpen={() =>
            navigate({
              name: 'day',
              date,
              tab: 'payments',
              ...(attention.unpaid.length ? { filter: 'unpaid' } : {}),
            })
          }
        />
      </Section>
      <AttentionList
        timezone={session.timezone}
        attention={attention}
        onRequests={() => navigate({ name: 'day', date, tab: 'requests' })}
        onLift={(time) => navigate({ name: 'lift', date, time })}
      />
      {others.length > 0 && (
        <Section title={t.home.otherDays}>
          <Card flush>
            <List>
              {others.map((other) => (
                <Row
                  key={other.service_date}
                  title={`${capitalize(relativeDay(other.service_date, session.today))} · ${shortDate(other.service_date)}`}
                  subtitle={daySubtitle(other)}
                  onClick={() => navigate({ name: 'day', date: other.service_date, tab: 'lifts' })}
                />
              ))}
            </List>
          </Card>
        </Section>
      )}
    </>
  );
}

function NoDays() {
  const planning = usePlanning();
  const schedule = planning.data?.schedule;
  const weekday = (index: number) => dateLabel(`2024-01-0${index + 1}`, { weekday: 'long' }); // 1 Jan 2024 was a Monday.
  return (
    <>
      <PageHeader title={t.home.noDaysTitle} />
      <Card>
        <Empty
          icon={<CalendarPlus size={24} />}
          title={t.home.noDaysText}
          text={
            schedule?.enabled
              ? t.home.autoPublish(weekday(schedule.creation_weekday), schedule.creation_time)
              : t.home.manualPublish
          }
          action={
            <Button variant="primary" onClick={() => navigate({ name: 'planning' })}>
              {t.home.openPlanning}
            </Button>
          }
        />
      </Card>
    </>
  );
}

/** Finished days of the current month; hidden until there is something to show. */
function MonthSummary({ session }: { session: Session }) {
  const range = periodRange('month', session.today);
  const valid = range.start <= range.end && range.start < session.today;
  const analytics = useAnalytics(
    `/api/analytics?period=custom&start=${range.start}&end=${range.end}&personal=false`,
    valid,
  );
  const summary = analytics.data?.summary;
  if (!valid || !summary?.lifts) return null;
  return (
    <Section
      title={capitalize(dateLabel(range.start, { month: 'long', year: 'numeric' }))}
      action={
        <Button variant="ghost" size="sm" onClick={() => navigate({ name: 'analytics' })}>
          {t.nav.analytics}
          <ArrowRight size={16} />
        </Button>
      }
    >
      <Card className="month-card">
        <div>
          <b className="tabular">{money(summary.net_gel)}</b>
          <span>{t.home.reported}</span>
        </div>
        <div>
          <b className="tabular">{num(summary.seats)}</b>
          <span>{t.home.seats}</span>
        </div>
        <div>
          <b className="tabular">{num(summary.occupancy_pct)}%</b>
          <span>{t.home.occupancy}</span>
        </div>
      </Card>
    </Section>
  );
}
