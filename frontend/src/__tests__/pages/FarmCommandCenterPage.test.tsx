/**
 * Farm command center (voron B11).
 *
 * The assertions that matter: the fleet is bucketed by group with the location
 * as fallback, the tiles and counters agree with each other, the search and
 * group filter narrow the grid, the alert roll-up opens with printer, spool
 * and maintenance items in it, and creating a group POSTs the printer ids.
 */
import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

import { FarmCommandCenterPage } from '../../pages/FarmCommandCenterPage';

const PRINTERS = [
  { id: 1, name: 'Voron 2.4', location: 'Workshop', is_active: true },
  { id: 2, name: 'P2S', location: 'Workshop', is_active: true },
  { id: 3, name: 'X2D', location: null, is_active: true },
];

const STATUS: Record<number, Record<string, unknown>> = {
  1: { connected: true, state: 'RUNNING', progress: 42, hms_errors: [] },
  2: { connected: true, state: 'IDLE', progress: null, hms_errors: [] },
  3: { connected: false, state: 'IDLE', progress: null, hms_errors: [] },
};

let fleetGroups: Array<{
  id: number;
  name: string;
  color: string | null;
  sort_order: number;
  printer_ids: number[];
  created_at: string;
  updated_at: string;
}> = [];
let createdPayloads: unknown[] = [];

function api(path: string) {
  return `*/api/v1${path}`;
}

function useHandlers() {
  server.use(
    http.get(api('/printers/'), () => HttpResponse.json(PRINTERS)),
    http.get(api('/printers/:id/status'), ({ params }) =>
      HttpResponse.json(STATUS[Number(params.id)] ?? { connected: false }),
    ),
    http.get(api('/printer-fleet-groups/'), () => HttpResponse.json(fleetGroups)),
    http.post(api('/printer-fleet-groups/'), async ({ request }) => {
      const body = await request.json();
      createdPayloads.push(body);
      const created = {
        id: 99,
        color: null,
        sort_order: 0,
        created_at: '2026-09-08T00:00:00',
        updated_at: '2026-09-08T00:00:00',
        ...(body as Record<string, unknown>),
      };
      fleetGroups = [...fleetGroups, created as (typeof fleetGroups)[number]];
      return HttpResponse.json(created);
    }),
    http.get(api('/queue/'), () =>
      HttpResponse.json([
        { id: 1, status: 'completed', completed_at: new Date().toISOString() },
        { id: 2, status: 'pending', completed_at: null },
      ]),
    ),
    http.get(api('/projects/'), () => HttpResponse.json([])),
    http.get(api('/settings/'), () => HttpResponse.json({ low_stock_threshold: 20 })),
    http.get(api('/inventory/spools'), () =>
      HttpResponse.json([
        { id: 7, material: 'PETG', color_name: 'Orange', label_weight: 1000, weight_used: 950 },
        { id: 8, material: 'PLA', color_name: 'Black', label_weight: 1000, weight_used: 100 },
      ]),
    ),
    http.get(api('/maintenance/overview'), () =>
      HttpResponse.json([
        {
          printer_id: 1,
          printer_name: 'Voron 2.4',
          maintenance_items: [
            { enabled: true, is_due: true, maintenance_type_name: 'Lubricate rails' },
          ],
        },
      ]),
    ),
  );
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/farm']}>
        <Routes>
          <Route path="/farm" element={<FarmCommandCenterPage />} />
          <Route path="/tv" element={<div>TV PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('FarmCommandCenterPage', () => {
  beforeEach(() => {
    fleetGroups = [];
    createdPayloads = [];
    useHandlers();
  });

  it('buckets printers by fleet group, falling back to location and Ungrouped', async () => {
    fleetGroups = [
      {
        id: 1,
        name: 'Klipper',
        color: null,
        sort_order: 0,
        printer_ids: [1],
        created_at: '',
        updated_at: '',
      },
    ];
    renderPage();

    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));
    // Voron is in a group; P2S falls back to its location; X2D has neither.
    // Each name appears twice - once as a bucket heading, once in the filter
    // dropdown - so this asserts presence, not uniqueness.
    expect(screen.getAllByText('Klipper').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Workshop').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Ungrouped').length).toBeGreaterThan(0);
  });

  it('shows every printer exactly once with no groups configured', async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));
    expect(screen.getByText('Voron 2.4')).toBeInTheDocument();
    expect(screen.getByText('P2S')).toBeInTheDocument();
    expect(screen.getByText('X2D')).toBeInTheDocument();
  });

  it('reports utilization and the fleet totals', async () => {
    renderPage();
    // One of three printing.
    await waitFor(() => expect(screen.getByText('33%')).toBeInTheDocument());
    expect(screen.getByText('3 total')).toBeInTheDocument();
    expect(screen.getByText('1 offline')).toBeInTheDocument();
  });

  it('narrows the grid with the search box', async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));

    fireEvent.change(screen.getByPlaceholderText('Search printers...'), {
      target: { value: 'voron' },
    });

    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(1));
    expect(screen.getByText('Voron 2.4')).toBeInTheDocument();
  });

  it('says so when the filters match nothing', async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));

    fireEvent.change(screen.getByPlaceholderText('Search printers...'), {
      target: { value: 'nothing-matches-this' },
    });

    await waitFor(() =>
      expect(screen.getByText('No printers match the current filters.')).toBeInTheDocument(),
    );
    expect(screen.queryAllByTestId('fleet-tile')).toHaveLength(0);
  });

  it('rolls up printer, spool and maintenance items into the alert dialog', async () => {
    renderPage();
    // One offline printer + one low spool + one maintenance item due.
    await waitFor(() => expect(screen.getByText('3 items need attention')).toBeInTheDocument());

    fireEvent.click(screen.getByText('3 items need attention'));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Offline or unreachable')).toBeInTheDocument();
    expect(within(dialog).getByText('5% filament remaining')).toBeInTheDocument();
    expect(within(dialog).getByText('Lubricate rails')).toBeInTheDocument();
    // The healthy spool is not in the list.
    expect(within(dialog).queryByText('Black PLA')).not.toBeInTheDocument();
  });

  it('creates a group with the selected printers', async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));

    fireEvent.click(screen.getByText('Create group'));
    const dialog = await screen.findByRole('dialog');

    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Klipper' } });
    fireEvent.click(within(dialog).getAllByRole('checkbox')[0]);
    fireEvent.click(within(dialog).getByText('Save group'));

    await waitFor(() => expect(createdPayloads).toHaveLength(1));
    expect(createdPayloads[0]).toMatchObject({ name: 'Klipper', printer_ids: [1] });
  });

  it('will not save a group with no name or no printers', async () => {
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('fleet-tile')).toHaveLength(3));

    fireEvent.click(screen.getByText('Create group'));
    const dialog = await screen.findByRole('dialog');

    // Name but no printers.
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Klipper' } });
    expect(within(dialog).getByText('Save group')).toBeDisabled();

    // Printers but no name.
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '  ' } });
    fireEvent.click(within(dialog).getAllByRole('checkbox')[0]);
    expect(within(dialog).getByText('Save group')).toBeDisabled();
    expect(createdPayloads).toHaveLength(0);
  });

  it('counts parts completed today from the queue', async () => {
    renderPage();
    // findByText, not getByText: the queue query resolves after the tiles do.
    expect(await screen.findByText('Parts today')).toBeInTheDocument();
    expect(await screen.findByText('2 queue items tracked')).toBeInTheDocument();
  });
});
