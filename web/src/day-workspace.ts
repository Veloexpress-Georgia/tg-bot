import type { LiveDay } from './types';

export function dayAttention(day: LiveDay) {
  return {
    unpaid: (day.riders ?? []).filter((r) => r.due_now_gel > r.paid_gel),
    queues: day.lifts.filter((l) => !l.cancelled && l.waiting_count > 0),
    underfilled: day.lifts.filter((l) => !l.cancelled && !l.running),
    lateExits: day.late_exits,
    commands: (day.commands ?? []).filter((c) =>
      ['pending', 'running', 'review', 'failed'].includes(c.status),
    ),
  };
}
