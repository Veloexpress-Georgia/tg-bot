import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { loadSession, request } from '../api';
import type {
  Analytics,
  BookingOrder,
  CommandResult,
  DayDetail,
  LiveDay,
  MyDays,
  Planning,
  RefundReport,
} from '../types';

export const keys = {
  session: ['session'] as const,
  days: ['days'] as const,
  day: (date: string) => ['day', date] as const,
  order: (date: string, time: string) => ['booking-order', date, time] as const,
  analytics: (path: string) => ['analytics', path] as const,
  planning: ['planning'] as const,
  refunds: ['refunds'] as const,
  audit: ['audit'] as const,
  myDays: ['mydays'] as const,
  command: (id: number) => ['command', id] as const,
};

const dayPath = (date: string) => `/api/admin/days/${date}`;

export const useSession = () =>
  useQuery({ queryKey: keys.session, queryFn: loadSession, retry: false, staleTime: Infinity });

export const useLiveDays = (enabled = true) =>
  useQuery({
    queryKey: keys.days,
    queryFn: () => request<LiveDay[]>('/api/admin/days'),
    enabled,
    refetchInterval: 30_000,
  });

/**
 * A day page renders at once from the day list (lift counts and money) while
 * riders, payments and requests load; only those parts wait.
 */
export function useDay(date: string) {
  const client = useQueryClient();
  return useQuery<DayDetail>({
    queryKey: keys.day(date),
    queryFn: () => request<DayDetail>(dayPath(date)),
    refetchInterval: (query) => (query.state.data?.historical ? false : 30_000),
    placeholderData: () =>
      client.getQueryData<LiveDay[]>(keys.days)?.find((day) => day.service_date === date),
  });
}

/** Opening the day that matters most should not start with a wait. */
export function usePrefetchDay(date: string | undefined) {
  const client = useQueryClient();
  useEffect(() => {
    if (!date) return;
    void client.prefetchQuery({
      queryKey: keys.day(date),
      queryFn: () => request<DayDetail>(dayPath(date)),
      staleTime: 20_000,
    });
  }, [client, date]);
}

export const useBookingOrder = (date: string, time: string) =>
  useQuery({
    queryKey: keys.order(date, time),
    queryFn: () =>
      request<BookingOrder>(`${dayPath(date)}/lifts/${encodeURIComponent(time)}/order`),
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });

export const useAnalytics = (path: string, enabled = true) =>
  useQuery({
    queryKey: keys.analytics(path),
    queryFn: () => request<Analytics>(path),
    enabled,
    placeholderData: keepPreviousData,
  });

export const usePlanning = (enabled = true) =>
  useQuery({
    queryKey: keys.planning,
    queryFn: () => request<Planning>('/api/admin/planning'),
    enabled,
  });

export const useRefunds = () =>
  useQuery({
    queryKey: keys.refunds,
    queryFn: () => request<RefundReport[]>('/api/admin/refunds'),
  });

export const useAudit = () =>
  useQuery({
    queryKey: keys.audit,
    queryFn: () => request<CommandResult[]>('/api/admin/audit'),
    refetchInterval: 10_000,
  });

export const useMyDays = () =>
  useQuery({
    queryKey: keys.myDays,
    queryFn: () => request<MyDays>('/api/my-days'),
    refetchInterval: 30_000,
  });

/** Everything except identity and command polling. */
export function useRefreshAll() {
  const client = useQueryClient();
  return () =>
    client.invalidateQueries({
      predicate: (query) => !['session', 'command'].includes(String(query.queryKey[0])),
    });
}
