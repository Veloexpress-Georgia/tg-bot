import type {
  Analytics,
  DayDetail,
  LiveDay,
  Planning,
  MyDays,
  Summary,
  Session,
  Rider,
} from './types';
export const demoSession: Session = {
  user_id: 42,
  name: 'Мишо',
  admin: true,
  csrf: 'demo',
  timezone: 'Asia/Tbilisi',
  today: '2026-10-01',
};
const times = ['8:30', '10:00', '11:45', '13:30', '15:30'];
const names = [
  'Алексей М.',
  'Нино К.',
  'Георгий С.',
  'Анна В.',
  'Давид Л.',
  'Мария П.',
  'Илья Б.',
  'Тамара Д.',
  'Саша Р.',
  'Лука Г.',
  'Ксения А.',
  'Мишо',
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
  net: number;
  guests: number;
  manual: number;
  reversed: number;
}
const records: Record[] = [];
for (
  let d = new Date('2025-03-01T12:00:00Z');
  d < new Date('2026-10-01T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + 1)
) {
  if (![0, 6].includes(d.getUTCDay())) continue;
  const seed = Math.floor(d.getTime() / 86400000);
  times.slice(0, 4).forEach((time, i) => {
    const cancelled = (seed + i * 3) % 23 === 0;
    const seats = cancelled ? 0 : 5 + ((seed * 7 + i * 11) % 6);
    records.push({
      date: d.toISOString().slice(0, 10),
      time,
      seats,
      cancelled,
      net: seats * 20 - ((seed + i) % 9 === 0 ? 20 : 0),
      guests: cancelled ? 0 : (seed + i) % 3,
      manual: cancelled ? 0 : (seed + i) % 2,
      reversed: (seed + i) % 17 === 0 ? 20 : 0,
    });
  });
}
function summarize(rows: Record[], personal = false): Summary {
  const result = empty();
  const ran = rows.filter((r) => !r.cancelled);
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
  week_start: '2026-10-03',
  saturday_enabled: true,
  sunday_enabled: true,
  first_lift_time: '8:30',
  last_lift_time: '13:30',
  posted_dates: ['2026-10-03', '2026-10-04'],
  lift_times: times,
  schedule: {
    enabled: true,
    creation_weekday: 4,
    creation_time: '14:00',
    announce_lead_minutes: 120,
    skip_week_start: null,
  },
  terms: { price_gel: 20, deadline_time: '20:00', timezone: 'Asia/Tbilisi' },
};
export const demoDays: LiveDay[] = ['2026-10-03', '2026-10-04'].map((service_date, day) => ({
  service_date,
  booked_rider_count: 16 - day * 4,
  paid_rider_count: 12 - day * 3,
  expected_gel: 600 - day * 180,
  owed_gel: 120,
  past: false,
  running_count: 3,
  confirmed_seat_count: 30 - day * 9,
  waitlist: day
    ? []
    : [
        {
          telegram_user_id: 109,
          label: 'Лука Г.',
          lift_time: '8:30',
          position: 1,
        },
      ],
  late_exits: [],
  lifts: times.slice(0, 4).map((time, i) => {
    const occupied = [10, 9, 7, 4][i] - day;
    return {
      time,
      vote_count: occupied - 1 + (i === 0 && day === 0 ? 1 : 0),
      manual_count: 1,
      guest_count: 0,
      capacity: 10,
      cancelled: false,
      covered_count: Math.max(0, occupied - 2),
      seat_count: occupied,
      waiting_count: i === 0 && day === 0 ? 1 : 0,
      running: occupied >= 5,
      funded: occupied - 2 >= 5,
      riders: names.slice(0, occupied).map((label, j) => ({
        telegram_user_id: j === 7 ? 42 : 100 + j,
        label,
        paid: j < occupied - 2,
        cash: j % 5 === 0,
        guests: 0,
        waitlisted: false,
      })),
    };
  }),
  riders: names.slice(0, 10).map((label, i) => ({
    user_id: i === 7 ? 42 : 100 + i,
    label,
    service_date,
    price_gel: 20,
    paid_gel: i < 7 ? 40 : 20,
    due_now_gel: 40,
    due_all_gel: 40,
    payment_method: 'transfer',
    pending_lift_times: [],
    rows: [
      {
        lift_time: '8:30',
        guests: 0,
        seats_left: 0,
        waitlist_position: 0,
        running: true,
      },
      {
        lift_time: '10:00',
        guests: 0,
        seats_left: 1,
        waitlist_position: 0,
        running: true,
      },
    ],
  })),
}));
export const demoMyDays: MyDays = {
  days: demoDays.map((day) => ({
    service_date: day.service_date,
    past: false,
    lifts: day.lifts.map((l) => ({
      time: l.time,
      seats: l.seat_count,
      capacity: l.capacity,
      waiting: l.waiting_count,
      cancelled: l.cancelled,
      running: l.running,
    })),
    booking: { ...day.riders![0], paid_gel: day.service_date === '2026-10-04' ? 20 : 40 },
  })),
  polls_url: null,
  bank_details: 'Демонстрационные данные. Реквизиты для оплаты появятся в рабочем кабинете.',
};
export function demoDay(day: string): DayDetail {
  const live = demoDays.find((d) => d.service_date === day);
  if (live) return live;
  const rows = records.filter((r) => r.date === day);
  return {
    historical: true,
    service_date: day,
    price_gel: 20,
    received_gel: rows.reduce((n, r) => n + r.net + r.reversed, 0),
    refunded_gel: rows.reduce((n, r) => n + r.reversed, 0),
    cancelled: rows.every((r) => r.cancelled),
    reconstructed: false,
    lifts: rows.map((row) => ({
      lift_time: row.time,
      ran: !row.cancelled,
      seats: row.seats,
      capacity: 10,
      covered_seats: Math.floor(row.net / 20),
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
  if (url.pathname === '/api/admin/days') return demoDays;
  if (url.pathname.startsWith('/api/admin/days/')) return demoDay(url.pathname.split('/').at(-1)!);
  if (url.pathname === '/api/admin/planning') return demoPlanning;
  if (url.pathname === '/api/my-days') return demoMyDays;
  if (url.pathname === '/api/admin/audit') return [];
  if (url.pathname === '/api/admin/refunds')
    return [
      {
        service_date: '2026-09-20',
        lift_time: '13:30',
        text: 'Оценка возвратов при отмене выезда 13:30\nАнна В. — 20 GEL\nДавид Л. — 20 GEL\nВсего: 40 GEL\nЭто оценка, а не подтверждение возврата денег.',
        created_at: '2026-09-19T17:00:00Z',
      },
    ];
  if (url.pathname === '/api/analytics')
    return demoAnalytics(
      url.searchParams.get('period') === 'all'
        ? '2025-03-01'
        : (url.searchParams.get('start') ?? '2026-09-01'),
      url.searchParams.get('end') ?? '2026-09-30',
      url.searchParams.get('personal') === 'true',
    );
  throw new Error('Неизвестный демонстрационный экран');
}
