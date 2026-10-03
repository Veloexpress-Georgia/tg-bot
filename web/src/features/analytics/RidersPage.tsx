import { Bike, Search, UserPlus, Users } from 'lucide-react';
import { useState } from 'react';
import { useAnalytics } from '../../app/queries';
import { t } from '../../i18n';
import { dateLabel, money, num } from '../../lib';
import type { Session } from '../../types';
import {
  Card,
  Empty,
  List,
  Notice,
  Page,
  PageHeader,
  Query,
  Row,
  Section,
  Stat,
  StatGrid,
} from '../../ui';
import { PeriodToolbar } from './AnalyticsPage';
import { periodRequest, usePeriod } from './period';
import './analytics.css';

export function RidersPage({ session }: { session: Session }) {
  const [period, setPeriod] = usePeriod();
  const request = periodRequest(period, session.today, false);
  const analytics = useAnalytics(request.path, request.valid);
  const [search, setSearch] = useState('');
  return (
    <Page wide>
      <PageHeader title={t.riders.title} subtitle={t.riders.subtitle} />
      <PeriodToolbar
        value={period}
        onChange={setPeriod}
        range={request.range}
        today={session.today}
      />
      {!request.valid ? (
        <Notice tone="warn">{t.analytics.invalidRange}</Notice>
      ) : (
        <Query query={analytics}>
          {(data) => {
            const query = search.trim().toLocaleLowerCase();
            const riders = data.riders.filter((rider) =>
              rider.label.toLocaleLowerCase().includes(query),
            );
            return (
              <>
                <StatGrid>
                  <Stat
                    accent
                    label={t.riders.riders}
                    icon={<Users size={18} />}
                    value={num(data.summary.riders)}
                    note={t.riders.countedOnce}
                  />
                  <Stat
                    label={t.riders.newRiders}
                    icon={<UserPlus size={18} />}
                    value={num(data.summary.new_riders ?? 0)}
                    note={t.riders.newNote}
                  />
                  <Stat
                    label={t.analytics.guestSeats}
                    icon={<Users size={18} />}
                    value={num(data.summary.guests)}
                    note={t.riders.guestNote}
                  />
                  <Stat
                    label={t.riders.lifts}
                    icon={<Bike size={18} />}
                    value={num(data.summary.lifts)}
                    note={t.riders.days(data.summary.days)}
                  />
                </StatGrid>
                <Section title={t.riders.list}>
                  <label className="search">
                    <Search size={18} />
                    <input
                      type="search"
                      placeholder={t.riders.search}
                      aria-label={t.riders.search}
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                    />
                  </label>
                  {riders.length ? (
                    <Card flush>
                      <List>
                        {riders.map((rider) => (
                          <Row
                            key={rider.user_id}
                            leading={<span className="initial">{rider.label.slice(0, 1)}</span>}
                            title={
                              <>
                                {rider.label}
                                {rider.new && (
                                  <span className="badge badge-ok day-flag">{t.riders.new}</span>
                                )}
                              </>
                            }
                            subtitle={[
                              t.riders.activity(rider.days, rider.lifts, rider.guests),
                              rider.last_day && t.riders.lastDay(dateLabel(rider.last_day)),
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                            trailing={<span className="tabular">{money(rider.net_gel)}</span>}
                          />
                        ))}
                      </List>
                    </Card>
                  ) : (
                    <Card>
                      <Empty title={search ? t.riders.notFound : t.riders.empty} />
                    </Card>
                  )}
                  <p className="caption section-note">{t.riders.note}</p>
                </Section>
              </>
            );
          }}
        </Query>
      )}
    </Page>
  );
}
