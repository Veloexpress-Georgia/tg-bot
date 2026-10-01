import { afterEach, describe, expect, it, vi } from 'vitest';
import { plain } from './lib';
import type { CommandSpec } from './types';

afterEach(() => vi.unstubAllGlobals());

describe('cancellation preview response to confirmation text', () => {
  it.each(['cancel_lift', 'cancel_day'] as const)(
    'renders a nullable %s preview from the API without crashing',
    async (action) => {
      vi.stubGlobal('window', { location: { search: '' } });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(
          new Response(
            JSON.stringify({
              confirmation: 'signed-preview',
              details: null,
              affected: 5,
              service_date: '2026-10-03',
            }),
            { status: 200 },
          ),
        ),
      );
      const { previewCommand } = await import('./api');
      const spec: CommandSpec = {
        request_id: 'test-request',
        action,
        service_date: '2026-10-03',
        lift_time: '8:30',
      };
      const preview = await previewCommand(spec);
      expect(plain(preview.details)).toBe('');
      expect(preview.confirmation).toBe('signed-preview');
    },
  );
  it.each([
    [
      'Cancel lift 8:30 on 2026-10-03. No payments have been reported for this day.',
      'Cancel lift 8:30 on 2026-10-03. No payments have been reported for this day.',
    ],
    [
      'Cancel all lifts on 2026-10-03. Payment tracking is disabled; refund estimates are unavailable.',
      'Cancel all lifts on 2026-10-03. Payment tracking is disabled; refund estimates are unavailable.',
    ],
    ['<b>Total to return: 15 GEL</b>', 'Total to return: 15 GEL'],
  ])('renders the preview text: %s', async (details, expected) => {
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            confirmation: 'signed-preview',
            details,
          }),
          { status: 200 },
        ),
      ),
    );
    const { previewCommand } = await import('./api');
    const preview = await previewCommand({
      request_id: 'test-request',
      action: 'cancel_day',
      service_date: '2026-10-03',
    });
    expect(plain(preview.details)).toBe(expected);
  });
});
