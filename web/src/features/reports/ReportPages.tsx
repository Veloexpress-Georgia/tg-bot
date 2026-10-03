import { ArrowDownLeft, Check, Clock3, ShieldCheck, TriangleAlert } from 'lucide-react';
import { useAudit, useRefunds } from '../../app/queries';
import { t } from '../../i18n';
import { intlLocale } from '../../locale';
import { plain } from '../../lib';
import type { Action, CommandResult, Session } from '../../types';
import { Badge, Card, Empty, List, Notice, Page, PageHeader, Query, Row, Section } from '../../ui';
import { shortDate } from '../days/model';
import './reports.css';

const stamp = (value: string, timeZone?: string) =>
  new Date(value).toLocaleString(intlLocale(), {
    timeZone,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });

export function RefundsPage() {
  const refunds = useRefunds();
  return (
    <Page>
      <PageHeader title={t.refunds.title} subtitle={t.refunds.subtitle} />
      <Notice>{t.refunds.notice}</Notice>
      <Query query={refunds}>
        {(reports) =>
          reports.length ? (
            <div className="report-list">
              {reports.map((report, index) => (
                <Card key={index}>
                  <div className="report-head">
                    <strong>
                      {shortDate(report.service_date)} · {report.lift_time ?? t.refunds.wholeDay}
                    </strong>
                    <span className="caption">{stamp(report.created_at)}</span>
                  </div>
                  <pre className="report">{plain(report.text)}</pre>
                </Card>
              ))}
            </div>
          ) : (
            <Section>
              <Card>
                <Empty icon={<ArrowDownLeft size={24} />} title={t.refunds.empty} />
              </Card>
            </Section>
          )
        }
      </Query>
    </Page>
  );
}

export function AuditPage({ session }: { session: Session }) {
  const audit = useAudit();
  return (
    <Page>
      <PageHeader title={t.audit.title} subtitle={t.audit.subtitle} />
      <Query query={audit}>
        {(entries) => <AuditList entries={entries} timezone={session.timezone} />}
      </Query>
    </Page>
  );
}

export function AuditList({ entries, timezone }: { entries: CommandResult[]; timezone: string }) {
  if (!entries.length)
    return (
      <Card>
        <Empty icon={<ShieldCheck size={24} />} title={t.audit.empty} />
      </Card>
    );
  return (
    <Card flush className="audit-list">
      <List>
        {entries.map((entry) => {
          const warn = entry.status === 'failed' || entry.status === 'review';
          const where = [
            entry.service_date && shortDate(entry.service_date),
            entry.lift_time,
          ].filter(Boolean);
          return (
            <Row
              key={entry.id}
              leading={
                entry.status === 'complete' ? (
                  <Check size={18} />
                ) : warn ? (
                  <TriangleAlert size={18} />
                ) : (
                  <Clock3 size={18} />
                )
              }
              tone={entry.status === 'complete' ? 'ok' : warn ? 'warn' : undefined}
              title={
                <>
                  {t.actions[entry.action as Action] ?? entry.action}
                  {where.length > 0 && <span className="audit-where"> · {where.join(' · ')}</span>}
                </>
              }
              subtitle={
                <>
                  {entry.result ? plain(entry.result.message) : t.cmd.request(entry.id)}
                  <span className="audit-meta">
                    {stamp(entry.created_at, timezone)} ·{' '}
                    {entry.actor_name ?? `Telegram ID ${entry.actor_user_id}`}
                  </span>
                </>
              }
              trailing={
                <Badge tone={entry.status === 'complete' ? 'ok' : warn ? 'warn' : 'neutral'}>
                  {t.audit.status[entry.status]}
                </Badge>
              }
            />
          );
        })}
      </List>
    </Card>
  );
}
