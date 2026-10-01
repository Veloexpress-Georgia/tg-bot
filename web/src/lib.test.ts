import { describe, expect, it } from 'vitest';
import { delta, periodRange, plain } from './lib';
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
  it('never displays infinite percentage growth', () => {
    expect(delta(50, 0)).toBe('Первый результат');
    expect(delta(0, 0)).toBe('Без изменений');
    expect(delta(100, 50)).toContain('+100%');
  });
  it('renders Telegram HTML as text without injecting markup', () => {
    expect(plain('<b>Alice</b> &amp; &lt;script&gt;')).toBe('Alice & <script>');
  });
});
