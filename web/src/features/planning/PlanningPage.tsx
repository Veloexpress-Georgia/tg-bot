import { CalendarDays, Clock3, Plus, Send, Settings2, ShieldCheck } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useCommands } from '../../app/commands';
import { usePlanning } from '../../app/queries';
import { t } from '../../i18n';
import { addDays, capitalize, cx, dateLabel } from '../../lib';
import type { Planning, Session } from '../../types';
import {
  Button,
  Card,
  Field,
  Notice,
  Page,
  PageHeader,
  Query,
  Segmented,
  Select,
  useMedia,
} from '../../ui';
import './planning.css';

type Part = 'weekend' | 'schedule' | 'terms' | 'extra';
const weekdayName = (index: number) =>
  capitalize(dateLabel(addDays('2024-01-01', index), { weekday: 'long' })); // 1 Jan 2024 was a Monday.

export function PlanningPage({ session }: { session: Session }) {
  const planning = usePlanning();
  return (
    <Page wide>
      <PageHeader title={t.plan.title} subtitle={t.plan.subtitle} />
      <Query query={planning}>
        {(data) => <PlanningForms key={data.week_start} data={data} today={session.today} />}
      </Query>
    </Page>
  );
}

function PlanningForms({ data, today }: { data: Planning; today: string }) {
  const { act, busy } = useCommands();
  const wide = useMedia('(min-width: 900px)');
  const [part, setPart] = useState<Part>('weekend');
  const [saturday, setSaturday] = useState(data.saturday_enabled);
  const [sunday, setSunday] = useState(data.sunday_enabled);
  const [first, setFirst] = useState(data.first_lift_time);
  const [last, setLast] = useState(data.last_lift_time);
  const [price, setPrice] = useState(String(data.terms.price_gel));
  const [deadline, setDeadline] = useState(data.terms.deadline_time);
  const [enabled, setEnabled] = useState(data.schedule.enabled);
  const [weekday, setWeekday] = useState(data.schedule.creation_weekday);
  const [creation, setCreation] = useState(data.schedule.creation_time);
  const [lead, setLead] = useState(data.schedule.announce_lead_minutes);
  const [extra, setExtra] = useState(today);
  const [extraFirst, setExtraFirst] = useState('8:30');
  const [extraLast, setExtraLast] = useState('13:30');
  const show = (value: Part) => wide || part === value;
  const times = data.lift_times.map((time) => ({ value: time, label: time }));
  const locked = data.posted_dates.some(
    (day) => day === data.week_start || day === addDays(data.week_start, 1),
  );
  return (
    <>
      {!wide && (
        <Segmented
          className="planning-tabs"
          label={t.plan.sections}
          value={part}
          onChange={setPart}
          options={[
            { value: 'weekend', label: t.plan.parts.weekend },
            { value: 'schedule', label: t.plan.parts.schedule },
            { value: 'terms', label: t.plan.parts.terms },
            { value: 'extra', label: t.plan.parts.extra },
          ]}
        />
      )}
      <div className="planning-grid">
        {show('weekend') && (
          <PlanCard
            icon={<CalendarDays size={20} />}
            eyebrow={t.plan.nextWeekend}
            title={dateLabel(data.week_start, { day: 'numeric', month: 'long' })}
          >
            <p className="caption">{t.plan.weekendNote}</p>
            <form
              className="form"
              onSubmit={(event) => {
                event.preventDefault();
                act({
                  action: 'plan',
                  service_date: data.week_start,
                  saturday_enabled: saturday,
                  sunday_enabled: sunday,
                  first_lift_time: first,
                  last_lift_time: last,
                });
              }}
            >
              <div className="day-toggles">
                {(
                  [
                    [t.plan.saturday, saturday, setSaturday],
                    [t.plan.sunday, sunday, setSunday],
                  ] as const
                ).map(([label, checked, set]) => (
                  <label key={label} className={cx('day-toggle', checked && 'selected')}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={locked || busy}
                      onChange={(event) => set(event.target.checked)}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <div className="form-grid">
                <Field label={t.plan.firstLift} group>
                  <Select
                    label={t.plan.firstLift}
                    value={first}
                    disabled={locked || busy}
                    onValueChange={setFirst}
                    options={times}
                  />
                </Field>
                <Field label={t.plan.lastLift} group>
                  <Select
                    label={t.plan.lastLift}
                    value={last}
                    disabled={locked || busy}
                    onValueChange={setLast}
                    options={times}
                  />
                </Field>
              </div>
              {locked ? (
                <Notice icon={<ShieldCheck size={18} />}>{t.plan.locked}</Notice>
              ) : (
                <Button type="submit" disabled={busy || (!saturday && !sunday)}>
                  {t.plan.savePlan}
                </Button>
              )}
            </form>
            <div className="plan-publish">
              <Button variant="primary" disabled={busy} onClick={() => act({ action: 'post' })}>
                <Send size={16} />
                {t.plan.publishNow}
              </Button>
              <Button
                variant="ghost"
                className="btn-wrap"
                disabled={busy || !data.schedule.enabled}
                onClick={() => act({ action: 'skip' })}
              >
                {data.schedule.skip_week_start ? t.plan.resumeAuto : t.plan.skipAuto}
              </Button>
            </div>
          </PlanCard>
        )}
        {show('schedule') && (
          <PlanCard
            icon={<Clock3 size={20} />}
            eyebrow={t.plan.automatic}
            title={t.plan.scheduleTitle}
          >
            <form
              className="form"
              onSubmit={(event) => {
                event.preventDefault();
                act({
                  action: 'schedule',
                  enabled,
                  creation_weekday: weekday,
                  creation_time: creation,
                  announce_lead_minutes: lead,
                });
              }}
            >
              <label className="check">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
                {t.plan.publishAutomatically}
              </label>
              <div className="form-grid">
                <Field label={t.plan.weekday} group>
                  <Select
                    label={t.plan.weekday}
                    value={weekday}
                    onValueChange={setWeekday}
                    options={[0, 1, 2, 3, 4, 5].map((value) => ({
                      value,
                      label: weekdayName(value),
                    }))}
                  />
                </Field>
                <Field label={t.plan.time}>
                  <input
                    type="time"
                    required
                    value={creation}
                    onChange={(event) => setCreation(event.target.value)}
                  />
                </Field>
              </div>
              <Field label={t.plan.reminder} group>
                <Select
                  label={t.plan.reminder}
                  value={lead}
                  onValueChange={setLead}
                  options={[
                    { value: 0, label: t.plan.noReminder },
                    { value: 60, label: t.plan.hoursBefore(1) },
                    { value: 120, label: t.plan.hoursBefore(2) },
                    { value: 180, label: t.plan.hoursBefore(3) },
                  ]}
                />
              </Field>
              <Button type="submit" disabled={busy}>
                {t.plan.saveSchedule}
              </Button>
            </form>
          </PlanCard>
        )}
        {show('terms') && (
          <PlanCard
            icon={<Settings2 size={20} />}
            eyebrow={t.plan.newDays}
            title={t.plan.termsTitle}
          >
            <form
              className="form"
              onSubmit={(event) => {
                event.preventDefault();
                act({ action: 'terms', price_gel: Number(price), deadline_time: deadline });
              }}
            >
              <div className="form-grid">
                <Field label={t.plan.price}>
                  <input
                    type="number"
                    inputMode="numeric"
                    min="1"
                    max="10000"
                    step="1"
                    required
                    value={price}
                    onChange={(event) => setPrice(event.target.value)}
                  />
                </Field>
                <Field label={t.plan.deadline}>
                  <input
                    type="time"
                    required
                    value={deadline}
                    onChange={(event) => setDeadline(event.target.value)}
                  />
                </Field>
              </div>
              <Notice>{t.plan.termsNote}</Notice>
              <Button type="submit" disabled={busy || Number(price) <= 0}>
                {t.plan.saveTerms}
              </Button>
            </form>
            <p className="caption plan-zone">{t.plan.timezone(data.terms.timezone)}</p>
          </PlanCard>
        )}
        {show('extra') && (
          <PlanCard
            icon={<Plus size={20} />}
            eyebrow={t.plan.outsideSchedule}
            title={t.plan.extraTitle}
          >
            <p className="caption">{t.plan.extraNote}</p>
            <form
              className="form"
              onSubmit={(event) => {
                event.preventDefault();
                act({
                  action: 'extra',
                  service_date: extra,
                  first_lift_time: extraFirst,
                  last_lift_time: extraLast,
                });
              }}
            >
              <Field label={t.plan.date}>
                <input
                  type="date"
                  min={today}
                  max={addDays(today, 7)}
                  required
                  value={extra}
                  onChange={(event) => setExtra(event.target.value)}
                />
              </Field>
              <div className="form-grid">
                <Field label={t.plan.firstLift} group>
                  <Select
                    label={t.plan.firstLift}
                    value={extraFirst}
                    onValueChange={setExtraFirst}
                    options={times}
                  />
                </Field>
                <Field label={t.plan.lastLift} group>
                  <Select
                    label={t.plan.lastLift}
                    value={extraLast}
                    onValueChange={setExtraLast}
                    options={times}
                  />
                </Field>
              </div>
              <Button type="submit" disabled={busy}>
                <Plus size={16} />
                {t.plan.createDay}
              </Button>
            </form>
          </PlanCard>
        )}
      </div>
    </>
  );
}

function PlanCard({
  icon,
  eyebrow,
  title,
  children,
}: {
  icon: ReactNode;
  eyebrow: ReactNode;
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="plan-card">
      <div className="plan-card-head">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
        </div>
        <span className="plan-card-icon">{icon}</span>
      </div>
      {children}
    </Card>
  );
}
