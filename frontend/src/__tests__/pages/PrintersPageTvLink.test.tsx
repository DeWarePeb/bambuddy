/**
 * The Printers page toolbar links to TV mode (voron B10), next to the Cam Wall
 * controls, so the wall display is one click from where printers are managed.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
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

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

describe('PrintersPage — TV mode link', () => {
  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    server.use(
      http.get('/api/v1/printers/', () =>
        HttpResponse.json([
          {
            id: 1,
            name: 'X1C',
            ip_address: '192.168.1.100',
            serial_number: '01P00A000000001',
            model: 'X1C',
            is_active: true,
            location: null,
            auto_archive: true,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
          },
        ]),
      ),
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({
          connected: true,
          state: 'IDLE',
          progress: 0,
          temperatures: { nozzle: 25, bed: 25 },
          hms_errors: [],
          vt_tray: [],
          ams: [],
        }),
      ),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
    );
  });

  it('offers the TV mode page from the toolbar', async () => {
    render(<PrintersPage />);
    await waitFor(() => expect(document.getElementById('printer-card-1')).not.toBeNull());

    const link = screen.getAllByTitle('TV mode')[0];
    expect(link).toHaveAttribute('href', '/tv');
  });
});
