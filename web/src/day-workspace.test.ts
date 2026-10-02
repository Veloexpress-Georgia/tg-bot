import { describe, expect, it } from 'vitest';
import { dayAttention } from './day-workspace';
import { demoDays } from './demo';

describe('day attention', () => {
  it('keeps queues, underfilled lifts and unreported amounts separate', () => {
    const day = structuredClone(demoDays[0]);
    const result = dayAttention(day);
    expect(result.queues.map((l) => l.time)).toEqual(['8:30']);
    expect(result.underfilled.map((l) => l.time)).toEqual(['13:30']);
    expect(result.unpaid.every((r) => r.due_now_gel > r.paid_gel)).toBe(true);
    expect(result.commands.map((c) => c.status)).toEqual(['review']);
  });
  it('does not flag cancelled lifts or covered payments', () => {
    const day = structuredClone(demoDays[0]);
    day.lifts.forEach((l) => {
      l.cancelled = true;
    });
    day.riders?.forEach((r) => {
      r.paid_gel = r.due_now_gel + 20;
    });
    day.commands = [];
    const result = dayAttention(day);
    expect(result.queues).toEqual([]);
    expect(result.underfilled).toEqual([]);
    expect(result.unpaid).toEqual([]);
    expect(result.commands).toEqual([]);
  });
});
