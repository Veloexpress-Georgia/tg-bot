import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { keys } from '../../app/queries';
import { RefundsPage } from './ReportPages';

vi.mock('../../api', () => ({ request: vi.fn() }));
vi.mock('../../app/commands', () => ({ useCommands: () => ({ act: vi.fn(), busy: false }) }));

it('shows payable refunds with rider, date, amount, method and payout action', () => {
  const client = new QueryClient();
  client.setQueryData([...keys.refunds, false], [{ id: 1, label: 'Anna', service_date: '2026-11-21', amount_gel: 30, method: 'transfer', paid_at: null }]);
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><RefundsPage /></QueryClientProvider>);
  expect(html).toContain('Anna');
  expect(html).toContain('30');
  expect(html).toContain('₾');
  expect(html).toContain('transfer');
  expect(html).toContain('Mark paid');
  expect(html).toContain('Paid history');
});
