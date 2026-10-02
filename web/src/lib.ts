export const number = (n: number) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(n);
export const money = (n: number) => `${number(n)} ₾`;
export function dateLabel(day: string, options?: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat('ru-RU', options ?? { day: 'numeric', month: 'short' }).format(
    new Date(`${day}T12:00:00Z`),
  );
}
export function delta(current: number, previous: number): string {
  if (!previous) return current ? 'Первый результат' : 'Без изменений';
  const change = ((current - previous) / Math.abs(previous)) * 100;
  return `${change > 0 ? '+' : ''}${number(change)}% к прошлому периоду`;
}
export function plain(text: string | null | undefined) {
  return (text ?? '')
    .replace(/<[^>]*>/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"');
}
const iso = (value: Date) => value.toISOString().slice(0, 10);
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
