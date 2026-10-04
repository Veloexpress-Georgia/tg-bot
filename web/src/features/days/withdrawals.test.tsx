import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { keys } from '../../app/queries';
import { demoDays, demoSession } from '../../demo';
import type { LiveDay } from '../../types';
import { DayPage } from './DayPage';
import { LiftPage } from './LiftPage';

vi.mock('../../api', () => ({ request: vi.fn(), loadSession: vi.fn() }));
vi.mock('../../app/commands', () => ({ useCommands: () => ({ act: vi.fn(), busy: false }) }));

function render(page: 'day' | 'lift', withdrawals?: LiveDay['withdrawals']) {
  const day = { ...structuredClone(demoDays[0]), withdrawals };
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  client.setQueryData(keys.day(day.service_date), day);
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      {page === 'day' ? (
        <DayPage
          route={{ name: 'day', date: day.service_date, tab: 'withdrawals' }}
          session={demoSession}
        />
      ) : (
        <LiftPage
          route={{ name: 'lift', date: day.service_date, time: '15:30' }}
          session={demoSession}
        />
      )}
    </QueryClientProvider>,
  );
}

describe('recorded withdrawals', () => {
  const entries = [
    {
      event_id: 1,
      telegram_user_id: 12,
      label: '@early_rider',
      lift_time: '15:30',
      changed_at: '2026-10-03T14:13:22Z',
      after_deadline: false,
    },
    {
      event_id: 2,
      telegram_user_id: 12,
      label: '@early_rider',
      lift_time: '15:30',
      changed_at: '2026-10-04T01:13:51Z',
      after_deadline: true,
    },
    {
      event_id: 2,
      telegram_user_id: 12,
      label: '@other_lift_rider',
      lift_time: '10:00',
      changed_at: '2026-10-04T01:13:51Z',
      after_deadline: true,
    },
  ];
  it('shows exact local times and both cancellations even when a rider rejoined', () => {
    const html = render('lift', entries);
    expect(html).toContain('18:13:22');
    expect(html).toContain('05:13:51');
    expect(html).toContain('Before deadline');
    expect(html).toContain('After deadline');
    expect(html.match(/@early_rider/g)).toHaveLength(2);
    expect(html).not.toContain('@other_lift_rider');
  });
  it('opens the whole day history separately from requests and payments', () => {
    const html = render('day', entries);
    expect(html).toContain('@other_lift_rider');
    expect(html).toContain('10:00');
    expect(html).toContain('15:30');
    expect(html).toContain('05:13:51');
  });
  it('distinguishes an empty history from details still loading', () => {
    expect(render('lift', [])).toContain('No recorded withdrawals');
    expect(render('lift')).not.toContain('No recorded withdrawals');
  });
});
