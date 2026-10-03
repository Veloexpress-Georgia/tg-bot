import { describe, expect, it } from 'vitest';
import { clockMinutes, daysBetween, liftMinutes, periodRange, plain } from './lib';
describe('historical period boundaries', () => {
  it('shows the completed previous month on the first day', () => {
    expect(periodRange('month', '2026-10-01')).toEqual({ start: '2026-09-01', end: '2026-09-30' });
    expect(periodRange('month', '2026-10-01', -1)).toEqual({
      start: '2026-08-01',
      end: '2026-08-31',
    });
  });
  it('does not include live today and handles year rollover', () => {
    expect(periodRange('month', '2026-10-12')).toEqual({ start: '2026-10-01', end: '2026-10-11' });
    expect(periodRange('year', '2026-01-01')).toEqual({ start: '2025-01-01', end: '2025-12-31' });
    expect(periodRange('month', '2026-03-01')).toEqual({ start: '2026-02-01', end: '2026-02-28' });
  });
  it('renders Telegram HTML as text without injecting markup', () => {
    expect(plain('<b>Alice</b> &amp; &lt;script&gt;')).toBe('Alice & <script>');
  });
  it('accepts absent report text', () => {
    expect(plain(null)).toBe('');
    expect(plain(undefined)).toBe('');
  });
});
describe('clock and calendar helpers', () => {
  it('reads lift times and the club clock in its own timezone', () => {
    expect(liftMinutes('8:30')).toBe(510);
    expect(liftMinutes('15:30')).toBe(930);
    expect(clockMinutes('Asia/Tbilisi', new Date('2026-10-04T07:45:00Z'))).toBe(11 * 60 + 45);
  });
  it('counts calendar days across month ends', () => {
    expect(daysBetween('2026-09-30', '2026-10-04')).toBe(4);
    expect(daysBetween('2026-10-04', '2026-10-03')).toBe(-1);
  });
});
