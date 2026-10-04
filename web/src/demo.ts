import type {
  Analytics,
  DayDetail,
  DemandCounts,
  LiftRider,
  LiveDay,
  MyDays,
  Planning,
  Rider,
  RiderBooking,
  Session,
  Summary,
} from './types';

/** Synthetic data for ?demo=1. It is a Sunday lift day, like a real one. */
export const demoSession: Session = {
  user_id: 42,
  name: 'Misho',
  admin: true,
  csrf: 'demo',
  timezone: 'Asia/Tbilisi',
  today: '2026-10-04',
};
const times = ['8:30', '10:00', '11:45', '13:30', '15:30'];
const names = [
  '@alexey_m',
  '@nino_k',
  '@giorgi_s',
  '@anna_v',
  '@davit_l',
  '@maria_p',
  '@ilya_b',
  '@tamar_d',
  '@sasha_r',
  '@luka_g',
  '@ksenia_a',
  '@misho',
];
const empty = (): Summary => ({
  days: 0,
  lifts: 0,
  seats: 0,
  capacity: 0,
  occupancy_pct: 0,
  riders: 0,
  guests: 0,
  manual: 0,
  expected_gel: 0,
  received_gel: 0,
  reversed_gel: 0,
  net_gel: 0,
  cash_gel: 0,
  transfer_gel: 0,
  unknown_gel: 0,
  gap_gel: 0,
  cancelled_lifts: 0,
  backfilled_days: 0,
  new_riders: 0,
});
interface Record {
  date: string;
  time: string;
  seats: number;
  cancelled: boolean;
  ran: boolean;
  waiting: number | null;
  net: number;
  guests: number;
  manual: number;
  reversed: number;
}
const records: Record[] = [];
for (
  let d = new Date('2025-03-01T12:00:00Z');
  d < new Date('2026-10-04T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + 1)
) {
  if (![0, 6].includes(d.getUTCDay())) continue;
  const seed = Math.floor(d.getTime() / 86400000);
  times.slice(0, 4).forEach((time, i) => {
    const cancelled = (seed + i * 3) % 23 === 0;
    const seats = cancelled ? 0 : i === 3 && seed % 4 === 0 ? 3 : 5 + ((seed * 7 + i * 11) % 6);
    const ran = !cancelled && seats >= 5;
    records.push({
      date: d.toISOString().slice(0, 10),
      time,
      seats,
      cancelled,
      ran,
      waiting: d >= new Date('2026-09-01T00:00:00Z') ? (seats === 10 ? 1 + (seed % 4) : 0) : null,
      net: ran ? seats * 20 - ((seed + i) % 9 === 0 ? 20 : 0) : 0,
      guests: cancelled ? 0 : (seed + i) % 3,
      manual: cancelled ? 0 : (seed + i) % 2,
      reversed: (seed + i) % 17 === 0 ? 20 : 0,
    });
  });
}
function summarize(rows: Record[], personal = false): Summary {
  const result = empty();
  const ran = rows.filter((r) => r.ran && !r.cancelled);
  result.days = new Set(ran.map((r) => r.date)).size;
  result.lifts = ran.length;
  result.seats = personal ? ran.length : ran.reduce((n, r) => n + r.seats, 0);
  result.capacity = ran.length * 10;
  result.occupancy_pct = result.capacity
    ? Math.round((result.seats / result.capacity) * 1000) / 10
    : 0;
  result.riders = ran.length ? (personal ? 1 : Math.min(48, Math.floor(result.seats / 2.3))) : 0;
  result.guests = personal ? 0 : ran.reduce((n, r) => n + r.guests, 0);
  result.manual = personal ? 0 : ran.reduce((n, r) => n + r.manual, 0);
  result.expected_gel = result.seats * 20;
  result.net_gel = personal ? result.seats * 20 : rows.reduce((n, r) => n + r.net, 0);
  result.reversed_gel = rows.reduce((n, r) => n + r.reversed, 0);
  result.received_gel = result.net_gel + result.reversed_gel;
  result.cash_gel = Math.floor((result.net_gel * 0.2) / 20) * 20;
  result.transfer_gel = result.net_gel - result.cash_gel;
  result.gap_gel = Math.max(result.expected_gel - result.net_gel, 0);
  result.cancelled_lifts = rows.filter((r) => r.cancelled).length;
  result.new_riders = result.riders ? Math.max(1, Math.floor(result.riders * 0.18)) : 0;
  return result;
}
function demoDemandCounts(rows: Record[]): DemandCounts {
  const offered = rows.filter((r) => !r.cancelled);
  const known = offered.filter((r) => r.waiting !== null);
  const seats = offered.reduce((n, r) => n + r.seats, 0);
  return {
    offered_lifts: offered.length,
    ran_lifts: offered.filter((r) => r.ran).length,
    not_run_lifts: offered.filter((r) => !r.ran).length,
    booked_seats: seats,
    capacity: offered.length * 10,
    occupancy_pct: offered.length ? Math.round((seats / (offered.length * 10)) * 1000) / 10 : 0,
    full_lifts: offered.filter((r) => r.seats >= 10).length,
    free_seats: offered.reduce((n, r) => n + 10 - r.seats, 0),
    queue_recorded_lifts: known.length,
    queue_unknown_lifts: offered.length - known.length,
    queued_lifts: known.filter((r) => (r.waiting ?? 0) > 0).length,
    waiting_total: known.length ? known.reduce((n, r) => n + (r.waiting ?? 0), 0) : null,
  };
}
export function demoAnalytics(start: string, end: string, personal = false): Analytics {
  const own = personal ? records.filter((_, i) => i % 7 === 0) : records;
  const rows = own.filter((r) => r.date >= start && r.date <= end);
  const startDate = new Date(`${start}T12:00:00Z`),
    endDate = new Date(`${end}T12:00:00Z`);
  const days = Math.round((endDate.getTime() - startDate.getTime()) / 86400000) + 1;
  const prevEnd = new Date(startDate);
  prevEnd.setUTCDate(prevEnd.getUTCDate() - 1);
  const prevStart = new Date(startDate);
  prevStart.setUTCDate(prevStart.getUTCDate() - days);
  const previous = summarize(
    own.filter(
      (r) =>
        r.date >= prevStart.toISOString().slice(0, 10) &&
        r.date <= prevEnd.toISOString().slice(0, 10),
    ),
    personal,
  );
  const granularity = days <= 63 ? 'day' : days <= 1462 ? 'month' : 'year';
  const bucket = (value: string) =>
    granularity === 'day'
      ? value
      : granularity === 'month'
        ? `${value.slice(0, 7)}-01`
        : `${value.slice(0, 4)}-01-01`;
  const series: Analytics['series'] = [];
  const cursor = new Date(`${bucket(start)}T12:00:00Z`);
  while (cursor <= endDate) {
    const key = cursor.toISOString().slice(0, 10);
    series.push({
      date: key,
      ...summarize(
        rows.filter((r) => bucket(r.date) === key),
        personal,
      ),
    });
    if (granularity === 'day') cursor.setUTCDate(cursor.getUTCDate() + 1);
    else if (granularity === 'month') cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    else cursor.setUTCFullYear(cursor.getUTCFullYear() + 1);
  }
  const riders: Rider[] = names.map((label, i) => ({
    user_id: i === 11 ? 42 : 100 + i,
    label,
    days: Math.max(0, Math.ceil(rows.filter((r) => !r.cancelled).length / (4 + (i % 4)))),
    lifts: Math.max(0, Math.ceil(rows.filter((r) => !r.cancelled).length / (2 + (i % 4)))),
    guests: i % 3,
    net_gel: 20 * Math.max(0, Math.ceil(rows.filter((r) => !r.cancelled).length / (2 + (i % 4)))),
    last_day: rows.at(-1)?.date ?? null,
    new: i > 9,
  }));
  return {
    start,
    end,
    first_record: '2025-03-01',
    granularity,
    summary: summarize(rows, personal),
    previous,
    series,
    by_time: times.slice(0, 4).map((time) => ({
      time,
      ...summarize(
        rows.filter((r) => r.time === time),
        personal,
      ),
    })),
    demand: personal
      ? null
      : {
          summary: demoDemandCounts(rows),
          by_time: times
            .filter((time) => rows.some((r) => r.time === time && !r.cancelled))
            .map((time) => ({
              time,
              ...demoDemandCounts(rows.filter((r) => r.time === time)),
              days: rows
                .filter((r) => r.time === time && !r.cancelled)
                .reverse()
                .map((r) => ({
                  date: r.date,
                  seats: r.seats,
                  capacity: 10,
                  ran: r.ran,
                  waiting_count: r.waiting,
                  reconstructed: false,
                })),
            })),
        },
    riders: personal ? riders.filter((r) => r.user_id === 42) : rows.length ? riders : [],
    days: [...new Set(rows.map((r) => r.date))].reverse().map((date) => ({
      date,
      ...summarize(
        rows.filter((r) => r.date === date),
        personal,
      ),
    })),
  };
}
export const demoPlanning: Planning = {
  week_start: '2026-10-10',
  saturday_enabled: true,
  sunday_enabled: true,
  first_lift_time: '8:30',
  last_lift_time: '15:30',
  posted_dates: [],
  lift_times: times,
  schedule: {
    enabled: true,
    creation_weekday: 3,
    creation_time: '14:00',
    announce_lead_minutes: 120,
    skip_week_start: null,
  },
  terms: { price_gel: 20, deadline_time: '20:00', timezone: 'Asia/Tbilisi' },
};

const PRICE = 20;
const MINIMUM = 5;
const riderIds = new Map<string, number>();
const idOf = (label: string) => {
  if (label === '@misho') return 42;
  if (!riderIds.has(label)) riderIds.set(label, 100 + riderIds.size);
  return riderIds.get(label)!;
};
const pool = [
  '@levan_k',
  '@mariam_t',
  '@irakli_p',
  '@sopho_g',
  '@nika_z',
  '@dato_b',
  '@keti_m',
  '@zura_a',
  '@ana_b',
  '@vakho_t',
  '@tornike',
  '@salome_j',
  '@beka_r',
  '@lasha_d',
  '@eka_n',
  '@gio_m',
  '@tamuna',
  '@shota_k',
  '@nata_l',
  '@rati_g',
  '@lika_s',
  '@temo_v',
  '@nino_k',
  '@giorgi_s',
  '@anna_v',
  '@davit_l',
  '@maria_p',
  '@ilya_b',
];
const pick = (from: number, count: number) => pool.slice(from, from + count);
// Mirrors a busy Sunday: one lift short of the minimum, full lifts with waitlists.
const todayLifts = [
  { time: '8:30', manual: 0, guests: 0, seated: pick(0, 2), waiting: [] as string[] },
  { time: '10:00', manual: 1, guests: 0, seated: pick(2, 9), waiting: [] },
  { time: '11:45', manual: 0, guests: 0, seated: pick(11, 10), waiting: pick(21, 4) },
  {
    time: '13:30',
    manual: 0,
    guests: 1,
    seated: [...pick(5, 6), ...pick(16, 3)],
    waiting: pick(25, 2),
  },
  { time: '15:30', manual: 0, guests: 0, seated: [...pick(12, 6), '@misho'], waiting: [] },
];
const hostOfGuest = '@dato_b';

function buildDay(service_date: string): LiveDay {
  const lifts = todayLifts.map((lift) => {
    const seat_count = lift.seated.length + lift.manual + lift.guests;
    return { ...lift, seat_count, running: seat_count >= MINIMUM, capacity: 10 };
  });
  const labels = [...new Set(lifts.flatMap((lift) => [...lift.seated, ...lift.waiting]))];
  const riders = labels.map((label, index) => {
    const user_id = idOf(label);
    const rows = lifts
      .filter((lift) => lift.seated.includes(label) || lift.waiting.includes(label))
      .map((lift) => ({
        lift_time: lift.time,
        guests: label === hostOfGuest && lift.guests ? lift.guests : 0,
        seats_left: Math.max(lift.capacity - lift.seat_count, 0),
        waitlist_position: lift.waiting.indexOf(label) + 1,
        running: lift.running,
      }));
    const seated = rows.filter((row) => !row.waitlist_position);
    const confirmed = seated.filter((row) => row.running);
    const pending = seated.filter((row) => !row.running).map((row) => row.lift_time);
    const guests = rows.reduce((sum, row) => sum + row.guests, 0);
    const due_now_gel = (confirmed.length + guests) * PRICE;
    const due_all_gel = (seated.length + guests) * PRICE;
    // Cash is reported like a transfer and handed over on site.
    const cash = index % 4 === 0 || index % 9 === 4;
    const unpaid = index % 6 === 5 || label === '@misho';
    const booking: RiderBooking = {
      service_date,
      price_gel: PRICE,
      paid_gel: unpaid ? 0 : due_now_gel,
      due_now_gel,
      due_all_gel,
      payment_method: unpaid || !due_now_gel ? null : cash ? 'cash' : 'transfer',
      pending_lift_times: pending,
      rows,
    };
    return { user_id, label, ...booking };
  });
  const byLabel = new Map(riders.map((rider) => [rider.label, rider]));
  const liftRiders = (lift: (typeof lifts)[number]): LiftRider[] => [
    ...[...lift.seated, ...lift.waiting].map((label) => {
      const rider = byLabel.get(label)!;
      return {
        telegram_user_id: rider.user_id,
        label,
        paid: rider.paid_gel > 0 && rider.paid_gel >= rider.due_now_gel,
        cash: rider.payment_method === 'cash',
        guests: label === hostOfGuest ? lift.guests : 0,
        waitlisted: lift.waiting.includes(label),
      };
    }),
  ];
  return {
    service_date,
    booked_rider_count: riders.length,
    paid_rider_count: riders.filter((rider) => rider.paid_gel > 0).length,
    expected_gel: riders.reduce((sum, rider) => sum + rider.paid_gel, 0),
    owed_gel:
      lifts
        .filter((lift) => lift.running)
        .reduce((sum, lift) => sum + Math.min(lift.seat_count, lift.capacity), 0) * PRICE,
    past: false,
    running_count: lifts.filter((lift) => lift.running).length,
    confirmed_seat_count: lifts.reduce(
      (sum, lift) => sum + Math.min(lift.seat_count, lift.capacity),
      0,
    ),
    waitlist: lifts.flatMap((lift) =>
      lift.waiting.map((label, index) => ({
        telegram_user_id: idOf(label),
        label,
        lift_time: lift.time,
        position: index + 1,
      })),
    ),
    late_exits: [
      {
        telegram_user_id: idOf('@irakli_p'),
        label: '@irakli_p',
        lift_time: '10:00',
        changed_at: '2026-10-04T04:09:20Z',
      },
    ],
    withdrawals: [
      {
        event_id: 3,
        telegram_user_id: idOf('@irakli_p'),
        label: '@irakli_p',
        lift_time: '10:00',
        changed_at: '2026-10-04T04:09:20Z',
        after_deadline: true,
      },
      {
        event_id: 2,
        telegram_user_id: idOf('@sandro_v'),
        label: '@sandro_v',
        lift_time: '15:30',
        changed_at: '2026-10-04T01:13:51Z',
        after_deadline: true,
      },
      {
        event_id: 1,
        telegram_user_id: idOf('@niko_t'),
        label: '@niko_t',
        lift_time: '15:30',
        changed_at: '2026-10-03T14:13:22Z',
        after_deadline: false,
      },
    ],
    commands: [
      {
        id: 900,
        action: 'manual',
        actor_user_id: 42,
        actor_name: 'Misho',
        service_date,
        lift_time: '10:00',
        status: 'review',
        result: { message: 'Demo request whose result needs checking.' },
        created_at: '2026-10-04T05:40:00Z',
        finished_at: null,
      },
    ],
    lifts: lifts.map((lift) => ({
      time: lift.time,
      vote_count: lift.seated.length + lift.waiting.length,
      manual_count: lift.manual,
      guest_count: lift.guests,
      capacity: lift.capacity,
      cancelled: false,
      covered_count: liftRiders(lift).filter((rider) => rider.paid && !rider.waitlisted).length,
      seat_count: lift.seat_count,
      waiting_count: lift.waiting.length,
      running: lift.running,
      funded:
        liftRiders(lift).filter((rider) => rider.paid && !rider.waitlisted).length + lift.manual >=
        MINIMUM,
      riders: liftRiders(lift),
    })),
    riders,
  };
}

export const demoDays: LiveDay[] = [buildDay('2026-10-04')];
const demoSummaryDays = demoDays.map(({ riders: _riders, commands: _commands, ...day }) => ({
  ...day,
  lifts: day.lifts.map(({ riders: _liftRiders, ...lift }) => lift),
}));

export const demoMyDays: MyDays = {
  days: demoDays.map((day) => ({
    service_date: day.service_date,
    past: day.past,
    lifts: day.lifts.map((lift) => ({
      time: lift.time,
      seats: lift.seat_count,
      capacity: lift.capacity,
      waiting: lift.waiting_count,
      cancelled: lift.cancelled,
      running: lift.running,
    })),
    booking: day.riders?.find((rider) => rider.user_id === 42) ?? null,
  })),
  polls_url: null,
  bank_details: 'Demo data. Payment details appear in the working cabinet.',
};

export function demoDay(day: string): DayDetail {
  const live = demoDays.find((d) => d.service_date === day);
  if (live) return live;
  const rows = records.filter((r) => r.date === day);
  return {
    historical: true,
    service_date: day,
    price_gel: PRICE,
    received_gel: rows.reduce((n, r) => n + r.net + r.reversed, 0),
    refunded_gel: rows.reduce((n, r) => n + r.reversed, 0),
    cancelled: rows.length > 0 && rows.every((r) => r.cancelled),
    reconstructed: false,
    lifts: rows.map((row) => ({
      lift_time: row.time,
      ran: row.ran,
      waiting_count: row.waiting,
      seats: row.seats,
      capacity: 10,
      covered_seats: Math.floor(row.net / PRICE),
      manual_seats: row.manual,
      guest_seats: row.guests,
      riders: names
        .slice(0, row.seats)
        .map((label) => ({ label, seats: 1, guests: 0, covered_seats: 1 })),
    })),
  };
}

export function demoGet(path: string): unknown {
  const url = new URL(path, 'https://demo.local');
  if (url.pathname === '/api/session') return demoSession;
  if (url.pathname === '/api/admin/days') return demoSummaryDays;
  const orderMatch = url.pathname.match(/^\/api\/admin\/days\/([^/]+)\/lifts\/([^/]+)\/order$/);
  if (orderMatch) {
    const day = demoDay(orderMatch[1]);
    const lift = day.historical
      ? undefined
      : day.lifts.find((l) => l.time === decodeURIComponent(orderMatch[2]));
    const riders = day.historical ? [] : (day.riders ?? []);
    return {
      digest: 'demo',
      available_seats: Math.max(
        (lift?.capacity ?? 10) - (lift?.manual_count ?? 0) - (lift?.guest_count ?? 0),
        0,
      ),
      riders: (lift?.riders ?? []).map((r) => ({
        user_id: r.telegram_user_id,
        label: r.label,
        waitlisted: r.waitlisted,
        paid: r.paid,
        paid_gel: riders.find((person) => person.user_id === r.telegram_user_id)?.paid_gel ?? 0,
        cash: r.cash,
      })),
      previous_positions: lift?.time === '11:45' ? { [String(idOf('@temo_v'))]: 3 } : {},
      deadline_closed: true,
    };
  }
  if (url.pathname.startsWith('/api/admin/days/')) return demoDay(url.pathname.split('/').at(-1)!);
  if (url.pathname === '/api/admin/planning') return demoPlanning;
  if (url.pathname === '/api/my-days') return demoMyDays;
  if (url.pathname === '/api/admin/audit') return demoDays.flatMap((day) => day.commands ?? []);
  if (url.pathname === '/api/admin/refunds')
    return [
      {
        service_date: '2026-09-20',
        lift_time: '13:30',
        text: 'Refund estimate for cancelling lift 13:30\n@anna_v — 20 GEL\n@davit_l — 20 GEL\nTotal: 40 GEL\nThis is an estimate, not a confirmation that money was returned.',
        created_at: '2026-09-19T17:00:00Z',
      },
    ];
  if (url.pathname === '/api/config') return { browser_login: false, timezone: 'Asia/Tbilisi' };
  if (url.pathname === '/api/analytics')
    return demoAnalytics(
      url.searchParams.get('period') === 'all'
        ? '2025-03-01'
        : (url.searchParams.get('start') ?? '2026-09-01'),
      url.searchParams.get('end') ?? '2026-09-30',
      url.searchParams.get('personal') === 'true',
    );
  throw new Error('Unknown demo screen');
}
