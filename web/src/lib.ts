import { intlLocale } from './locale';

export const cx = (...names: (string | false | null | undefined)[]) =>
  names.filter(Boolean).join(' ');

export const num = (n: number) =>
  new Intl.NumberFormat(intlLocale(), { maximumFractionDigits: 1 }).format(n);
export const money = (n: number) => `${num(n)} ₾`;

/** Service days are calendar dates; format them at noon UTC so no zone moves the day. */
export function dateLabel(day: string, options?: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat(intlLocale(), {
    timeZone: 'UTC',
    ...(options ?? { day: 'numeric', month: 'short' }),
  }).format(new Date(`${day}T12:00:00Z`));
}
export const capitalize = (text: string) => text.charAt(0).toLocaleUpperCase() + text.slice(1);

export function plain(text: string | null | undefined) {
  return (text ?? '')
    .replace(/<[^>]*>/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"');
}

const iso = (value: Date) => value.toISOString().slice(0, 10);
export function addDays(day: string, days: number) {
  const value = new Date(`${day}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return iso(value);
}
export function daysBetween(from: string, to: string) {
  return Math.round(
    (new Date(`${to}T12:00:00Z`).getTime() - new Date(`${from}T12:00:00Z`).getTime()) / 86400000,
  );
}

export function periodRange(period: 'month' | 'year', today: string, offset = 0) {
  const now = new Date(`${today}T12:00:00Z`);
  let start: Date, end: Date;
  if (period === 'month') {
    const shift = now.getUTCDate() === 1 ? -1 : 0;
    start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset + shift, 1, 12));
    end = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0, 12));
  } else {
    const yearShift = now.getUTCMonth() === 0 && now.getUTCDate() === 1 ? -1 : 0;
    start = new Date(Date.UTC(now.getUTCFullYear() + offset + yearShift, 0, 1, 12));
    end = new Date(Date.UTC(now.getUTCFullYear() + offset + yearShift, 11, 31, 12));
  }
  const yesterday = new Date(now);
  yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  if (end > yesterday) end = yesterday;
  return { start: iso(start), end: iso(end) };
}

export function chartDate(day: string, granularity: string) {
  return dateLabel(
    day,
    granularity === 'year'
      ? { year: 'numeric' }
      : granularity === 'month'
        ? { month: 'short', year: '2-digit' }
        : { day: 'numeric', month: 'short' },
  );
}

/** Lift times are "H:MM" in the club's timezone. */
export function liftMinutes(time: string) {
  const [hours, minutes] = time.split(':').map(Number);
  return hours * 60 + minutes;
}
export function clockMinutes(timeZone: string, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const value = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  return value('hour') * 60 + value('minute');
}
