/**
 * TV / kiosk mode (voron B10).
 *
 * The assertions that matter: a tile says what is on the bed and how far along
 * it is, a Klipper printer with no AMS gets no spool block and no dead camera
 * frame, the strip counts printers by state, the clock ticks, and the refresh
 * cadence can be set from the header (and pinned from the URL for a kiosk).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render as rtlRender, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] } as { id: number } | null,
  authEnabled: true,
  requiresSetup: false,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshUser: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn(() => true),
  hasAnyPermission: vi.fn(() => true),
  hasAllPermissions: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { TvPage } from '../../pages/TvPage';
import { classifyTvState, resolveActiveTray } from '../../utils/tvMode';
import type { PrinterStatus } from '../../api/client';

const PRINTERS = [
  {
    id: 1,
    name: 'X1C-Lab',
    provider: 'bambu',
    serial_number: '01P00A000000001',
    ip_address: '192.168.1.10',
    model: 'X1C',
    location: 'Workshop',
    is_active: true,
    camera_rotation: 0,
    external_camera_enabled: false,
    nozzle_count: 1,
  },
  {
    id: 2,
    name: 'Voron 2.4',
    provider: 'klipper',
    serial_number: 'voron-01',
    ip_address: '192.168.1.11',
    api_url: 'http://192.168.1.11:7125',
    model: 'Voron 2.4',
    location: null,
    is_active: true,
    camera_rotation: 0,
    external_camera_enabled: false,
    nozzle_count: 1,
  },
];

const PRINTING_STATUS = {
  id: 1,
  name: 'X1C-Lab',
  connected: true,
  state: 'RUNNING',
  current_print: 'bracket_v3.3mf',
  subtask_name: 'bracket_v3',
  gcode_file: '/data/Metadata/plate_1.gcode',
  progress: 42,
  remaining_time: 33,
  layer_num: 120,
  total_layers: 300,
  hms_errors: [],
  tray_now: 1,
  ams: [
    {
      id: 0,
      tray: [
        { id: 0, tray_type: 'PETG', tray_sub_brands: 'PETG HF', tray_color: '00FF00FF', remain: 90 },
        { id: 1, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: 'FF0000FF', remain: 63 },
      ],
    },
  ],
  vt_tray: [],
};

const KLIPPER_IDLE_STATUS = {
  id: 2,
  name: 'Voron 2.4',
  connected: true,
  state: 'IDLE',
  current_print: null,
  progress: 0,
  remaining_time: 0,
  layer_num: 0,
  total_layers: 0,
  hms_errors: [],
  ams: [],
  vt_tray: [],
};

function useHandlers(statusById: Record<number, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(PRINTERS)),
    http.get('/api/v1/printers/:id/status', ({ params }) => {
      const status = statusById[Number(params.id)];
      return status ? HttpResponse.json(status) : new HttpResponse(null, { status: 404 });
    }),
  );
}

// The page renders outside the app layout and reads its query string, which
// the shared render() util (hard-coded BrowserRouter) cannot seed.
function renderAt(search = '') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/tv${search}`]}>
        <Routes>
          <Route path="/tv" element={<TvPage />} />
          <Route path="/login" element={<div>LOGIN PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const storage = new Map<string, string>();

describe('TvPage', () => {
  beforeEach(() => {
    mockUseAuth.user = { id: 1 };
    mockUseAuth.authEnabled = true;
    storage.clear();
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => storage.get(key) ?? null);
    vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
      storage.set(key, value);
    });
    useHandlers({ 1: PRINTING_STATUS, 2: KLIPPER_IDLE_STATUS });
  });

  afterEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
  });

  it('renders one status-first tile per printer', async () => {
    renderAt();
    const tile = await screen.findByTestId('tv-tile-1');

    expect(within(tile).getByRole('heading', { name: 'X1C-Lab' })).toBeInTheDocument();
    expect(within(tile).getByText('Printing')).toBeInTheDocument();
    expect(within(tile).getByText('bracket_v3.3mf')).toBeInTheDocument();
    expect(within(tile).getByText('42%')).toBeInTheDocument();
    expect(within(tile).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
    expect(within(tile).getByText('Layer 120/300')).toBeInTheDocument();
    expect(within(tile).getByText('33m left')).toBeInTheDocument();
    expect(within(tile).getByText(/^ETA \d{1,2}:\d{2}/)).toBeInTheDocument();
    // The spool feeding the hotend: tray_now=1 is slot 2 of AMS 0.
    const spool = within(tile).getByTestId('tv-spool-1');
    expect(spool).toHaveTextContent('PLA Basic');
    expect(spool).toHaveTextContent('63%');
    // Bambu printers carry a camera: the tile shows a snapshot frame.
    expect(within(tile).getByAltText('X1C-Lab')).toBeInTheDocument();
  });

  it('shows a Klipper printer without an AMS block or a dead camera frame', async () => {
    renderAt();
    const tile = await screen.findByTestId('tv-tile-2');

    await waitFor(() => expect(within(tile).getByText('Idle')).toBeInTheDocument());
    expect(within(tile).getByText('Ready to print')).toBeInTheDocument();
    expect(within(tile).queryByTestId('tv-spool-2')).not.toBeInTheDocument();
    expect(within(tile).queryByAltText('Voron 2.4')).not.toBeInTheDocument();
    expect(within(tile).queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('counts printers by state in the strip', async () => {
    renderAt();
    await screen.findByText('bracket_v3.3mf');

    await waitFor(() => {
      expect(screen.getByTestId('tv-count-printing')).toHaveTextContent('1');
      expect(screen.getByTestId('tv-count-idle')).toHaveTextContent('1');
    });
    expect(screen.getByTestId('tv-count-paused')).toHaveTextContent('0');
    expect(screen.getByTestId('tv-count-error')).toHaveTextContent('0');
    expect(screen.getByTestId('tv-count-offline')).toHaveTextContent('0');
    expect(screen.getByTestId('tv-status-strip')).toHaveTextContent('2 printers');
  });

  it('shows a clock', async () => {
    renderAt();
    expect(screen.getByTestId('tv-clock')).toHaveTextContent(/\d{1,2}:\d{2}:\d{2}/);
  });

  it('defaults the refresh interval to 5s, remembers a change, and honours ?refresh=', async () => {
    const { unmount } = renderAt();
    const select = screen.getByLabelText('Refresh') as HTMLSelectElement;
    expect(select.value).toBe('5');

    fireEvent.change(select, { target: { value: '30' } });
    expect(select.value).toBe('30');
    expect(localStorage.setItem).toHaveBeenCalledWith('tvRefreshSec', '30');
    unmount();

    // Remembered on the next visit…
    expect((screen.queryByLabelText('Refresh') as HTMLSelectElement | null)).toBeNull();
    const second = renderAt();
    expect((screen.getByLabelText('Refresh') as HTMLSelectElement).value).toBe('30');
    second.unmount();

    // …but a kiosk URL pins its own cadence.
    renderAt('?refresh=10');
    expect((screen.getByLabelText('Refresh') as HTMLSelectElement).value).toBe('10');
  });

  it('can switch the camera frames off for a status-only wall', async () => {
    renderAt();
    await screen.findByAltText('X1C-Lab');

    fireEvent.click(screen.getByLabelText('Cameras'));
    expect(screen.queryByAltText('X1C-Lab')).not.toBeInTheDocument();
    expect(localStorage.setItem).toHaveBeenCalledWith('tvCameras', '0');
  });

  it('ignores stale job metadata once a print has finished', async () => {
    useHandlers({
      1: { ...PRINTING_STATUS, state: 'FINISH', progress: 100 },
      2: KLIPPER_IDLE_STATUS,
    });
    renderAt();
    const tile = await screen.findByTestId('tv-tile-1');

    await waitFor(() => expect(within(tile).getByText('Finished')).toBeInTheDocument());
    expect(within(tile).queryByText('bracket_v3.3mf')).not.toBeInTheDocument();
    expect(within(tile).queryByText('100%')).not.toBeInTheDocument();
    expect(screen.getByTestId('tv-count-idle')).toHaveTextContent('2');
  });

  it('sends a signed-out visitor to /login — the page names files and spools', () => {
    mockUseAuth.user = null;
    renderAt();
    expect(screen.getByText('LOGIN PAGE')).toBeInTheDocument();
  });
});

describe('classifyTvState', () => {
  const base = { connected: true, hms_errors: [] } as unknown as PrinterStatus;

  it('buckets by connection, errors, then state', () => {
    expect(classifyTvState(undefined)).toBe('offline');
    expect(classifyTvState({ ...base, connected: false, state: 'RUNNING' })).toBe('offline');
    expect(classifyTvState({ ...base, state: 'RUNNING', hms_errors: [{ code: 'x' }] } as unknown as PrinterStatus)).toBe('error');
    expect(classifyTvState({ ...base, state: 'RUNNING' })).toBe('printing');
    expect(classifyTvState({ ...base, state: 'PAUSE' })).toBe('paused');
    expect(classifyTvState({ ...base, state: 'FINISH' })).toBe('finished');
    expect(classifyTvState({ ...base, state: 'FAILED' })).toBe('finished');
    expect(classifyTvState({ ...base, state: 'IDLE' })).toBe('idle');
  });
});

describe('resolveActiveTray', () => {
  it('finds the AMS slot, the AMS-HT unit and the external spool behind tray_now', () => {
    const status = PRINTING_STATUS as unknown as PrinterStatus;
    expect(resolveActiveTray(status)?.tray_sub_brands).toBe('PLA Basic');
    expect(resolveActiveTray({ ...status, tray_now: 255 })).toBeNull();
    expect(resolveActiveTray({ ...status, tray_now: undefined as unknown as number })).toBeNull();

    const ht = {
      ...status,
      tray_now: 128,
      ams: [...status.ams, { id: 128, tray: [{ id: 0, tray_type: 'ABS', tray_sub_brands: null, remain: 10 }] }],
    } as unknown as PrinterStatus;
    expect(resolveActiveTray(ht)?.tray_type).toBe('ABS');

    const ext = {
      ...status,
      tray_now: 254,
      vt_tray: [{ id: 254, tray_type: 'TPU', tray_sub_brands: 'TPU 95A', remain: -1 }],
    } as unknown as PrinterStatus;
    expect(resolveActiveTray(ext)?.tray_sub_brands).toBe('TPU 95A');
  });

  it('treats a loaded empty slot as no spool', () => {
    const status = {
      ...PRINTING_STATUS,
      tray_now: 0,
      ams: [{ id: 0, tray: [{ id: 0, tray_type: '', remain: 0 }] }],
    } as unknown as PrinterStatus;
    expect(resolveActiveTray(status)).toBeNull();
  });
});
