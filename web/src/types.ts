export interface Session {
  user_id: number;
  name: string;
  admin: boolean;
  csrf: string;
  timezone: string;
  today: string;
}
export interface Summary {
  days: number;
  lifts: number;
  seats: number;
  capacity: number;
  occupancy_pct: number;
  riders: number;
  guests: number;
  manual: number;
  expected_gel: number;
  received_gel: number;
  reversed_gel: number;
  net_gel: number;
  cash_gel: number;
  transfer_gel: number;
  unknown_gel: number;
  gap_gel: number;
  cancelled_lifts: number;
  backfilled_days: number;
  new_riders?: number;
}
export interface Rider {
  user_id: number;
  label: string;
  days: number;
  lifts: number;
  guests: number;
  net_gel: number;
  last_day: string | null;
  new: boolean;
}
export interface Analytics {
  start: string;
  end: string;
  first_record: string | null;
  granularity: 'day' | 'month' | 'year';
  summary: Summary;
  previous: Summary;
  series: (Summary & { date: string })[];
  by_time: (Summary & { time: string })[];
  demand: Demand | null;
  riders: Rider[];
  days: (Summary & { date: string })[];
}
export interface DemandCounts {
  offered_lifts: number;
  ran_lifts: number;
  not_run_lifts: number;
  booked_seats: number;
  capacity: number;
  occupancy_pct: number;
  full_lifts: number;
  free_seats: number;
  queue_recorded_lifts: number;
  queue_unknown_lifts: number;
  queued_lifts: number;
  waiting_total: number | null;
}
export interface Demand {
  summary: DemandCounts;
  by_time: (DemandCounts & {
    time: string;
    days: {
      date: string;
      seats: number;
      capacity: number;
      ran: boolean;
      waiting_count: number | null;
      reconstructed: boolean;
    }[];
  })[];
}
export interface LiftRider {
  telegram_user_id: number;
  label: string;
  paid: boolean;
  cash: boolean;
  guests: number;
  waitlisted: boolean;
}
export interface Lift {
  time: string;
  vote_count: number;
  manual_count: number;
  guest_count: number;
  capacity: number;
  cancelled: boolean;
  covered_count: number;
  seat_count: number;
  waiting_count: number;
  running: boolean;
  funded: boolean;
  riders?: LiftRider[];
}
export interface RiderBooking {
  service_date: string;
  price_gel: number;
  paid_gel: number;
  due_now_gel: number;
  due_all_gel: number;
  payment_method: string | null;
  pending_lift_times: string[];
  rows: {
    lift_time: string;
    guests: number;
    seats_left: number;
    waitlist_position: number;
    running: boolean;
  }[];
}
export interface BookingWithdrawal {
  event_id: number;
  telegram_user_id: number;
  label: string;
  lift_time: string;
  changed_at: string;
  after_deadline: boolean;
}
export interface LiveDay {
  service_date: string;
  lifts: Lift[];
  booked_rider_count: number;
  paid_rider_count: number;
  expected_gel: number;
  owed_gel: number;
  past: boolean;
  running_count: number;
  confirmed_seat_count: number;
  waitlist: {
    telegram_user_id: number;
    label: string;
    lift_time: string;
    position: number;
  }[];
  late_exits: {
    telegram_user_id: number;
    label: string;
    lift_time: string;
    changed_at?: string | null;
  }[];
  withdrawals?: BookingWithdrawal[];
  historical?: false;
  riders?: (RiderBooking & { user_id: number; label: string })[];
  commands?: CommandResult[];
}
export interface HistoricalDay {
  historical: true;
  service_date: string;
  price_gel: number;
  received_gel: number;
  refunded_gel: number;
  cancelled: boolean;
  reconstructed: boolean;
  lifts: {
    lift_time: string;
    ran: boolean;
    waiting_count: number | null;
    seats: number;
    capacity: number;
    covered_seats: number;
    manual_seats: number;
    guest_seats: number;
    riders: {
      label: string;
      seats: number;
      guests: number;
      covered_seats: number;
    }[];
  }[];
}
export type DayDetail = LiveDay | HistoricalDay;
export interface Planning {
  week_start: string;
  saturday_enabled: boolean;
  sunday_enabled: boolean;
  first_lift_time: string;
  last_lift_time: string;
  posted_dates: string[];
  lift_times: string[];
  schedule: {
    enabled: boolean;
    creation_weekday: number;
    creation_time: string;
    announce_lead_minutes: number;
    skip_week_start: string | null;
  };
  terms: { price_gel: number; deadline_time: string; timezone: string };
}
export type Action =
  | 'manual'
  | 'cancel_lift'
  | 'restore_lift'
  | 'cancel_day'
  | 'payment'
  | 'plan'
  | 'post'
  | 'extra'
  | 'terms'
  | 'schedule'
  | 'skip'
  | 'claim_payment'
  | 'guest'
  | 'undo_payment'
  | 'booking_order';
export interface BookingOrder {
  digest: string;
  available_seats: number;
  riders: {
    user_id: number;
    label: string;
    waitlisted: boolean;
    paid: boolean;
    paid_gel: number;
    /** The seat's payment was reported as cash, handed over on site. */
    cash: boolean;
  }[];
  previous_positions: Record<string, number>;
  deadline_closed: boolean;
}
export interface BookingOrderProposal extends BookingOrder {
  ordered_user_ids: number[];
  promoted: number[];
  demoted: number[];
  paid_demoted: number[];
}
export interface CommandSpec {
  action: Action;
  request_id?: string;
  service_date?: string;
  lift_time?: string;
  delta?: number;
  method?: 'cash' | 'transfer';
  saturday_enabled?: boolean;
  sunday_enabled?: boolean;
  first_lift_time?: string;
  last_lift_time?: string;
  price_gel?: number;
  deadline_time?: string;
  enabled?: boolean;
  creation_weekday?: number;
  creation_time?: string;
  announce_lead_minutes?: number;
  acknowledged?: boolean;
  include_pending?: boolean;
  ordered_user_ids?: number[];
  restore_user_id?: number;
}
export interface CommandResult {
  id: number;
  action: string;
  service_date?: string | null;
  lift_time?: string | null;
  actor_user_id: number;
  actor_name?: string;
  status: 'pending' | 'running' | 'complete' | 'failed' | 'review';
  result: {
    message: string;
    needs_confirmation?: boolean;
    report?: string;
  } | null;
  created_at: string;
  finished_at: string | null;
}
export interface Preview extends Partial<BookingOrderProposal> {
  confirmation: string;
  details: string;
  affected?: number;
  service_date?: string;
}
export interface MyDays {
  days: {
    service_date: string;
    past: boolean;
    lifts: {
      time: string;
      seats: number;
      capacity: number;
      waiting: number;
      cancelled: boolean;
      running: boolean;
    }[];
    booking: RiderBooking | null;
  }[];
  polls_url: string | null;
  bank_details: string;
}
export interface RefundReport {
  service_date: string;
  lift_time: string | null;
  text: string;
  created_at: string;
}
