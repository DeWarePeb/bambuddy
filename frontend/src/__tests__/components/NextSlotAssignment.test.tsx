/**
 * Tests for the "assign to the next loaded AMS slot" controls (voron B8):
 * the waiting badge with its cancel, the row action that opens the modal,
 * and the modal's printer / timeout choices reaching the API.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';

vi.mock('../../api/client', () => ({
  api: {
    getPrinters: vi.fn(),
    createPendingSlotAssignment: vi.fn(),
    cancelPendingSlotAssignment: vi.fn(),
    getPendingSlotAssignments: vi.fn().mockResolvedValue([]),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getSettings: vi.fn().mockResolvedValue({}),
  },
}));

import { AssignNextSlotModal, NextSlotBadge, NextSlotButton } from '../../components/NextSlotAssignment';
import { api } from '../../api/client';

const SPOOL = {
  id: 42,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Sunlu',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 0,
  archived_at: null,
  k_profiles: [],
};

const PENDING = {
  id: 7,
  spool_id: 42,
  printer_id: 1,
  printer_name: 'P2S',
  source: 'ui',
  status: 'pending' as const,
  timeout_seconds: 1800,
  created_at: '2026-09-07T10:00:00',
  expires_at: '2026-09-07T10:30:00',
  completed_at: null,
  assigned_printer_id: null,
  assigned_ams_id: null,
  assigned_tray_id: null,
};

const PRINTERS = [
  { id: 1, name: 'P2S' },
  { id: 2, name: 'X2D' },
];

describe('NextSlotBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.cancelPendingSlotAssignment).mockResolvedValue({ ...PENDING, status: 'cancelled' } as never);
  });

  it('names the printer it is waiting for and cancels through the API', async () => {
    const user = userEvent.setup();
    render(<NextSlotBadge pending={PENDING as never} />);

    expect(screen.getByText('Next slot on P2S')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /cancel next-slot assignment/i }));

    await waitFor(() => {
      expect(api.cancelPendingSlotAssignment).toHaveBeenCalledWith(7);
    });
  });

  it('says "any printer" for an unpinned request', () => {
    render(<NextSlotBadge pending={{ ...PENDING, printer_id: null, printer_name: null } as never} />);
    expect(screen.getByText('Next slot on any printer')).toBeInTheDocument();
  });
});

describe('AssignNextSlotModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPrinters).mockResolvedValue(PRINTERS as never);
    vi.mocked(api.createPendingSlotAssignment).mockResolvedValue(PENDING as never);
  });

  it('renders nothing when closed', () => {
    render(<AssignNextSlotModal isOpen={false} onClose={vi.fn()} spool={SPOOL as never} />);
    expect(screen.queryByTestId('next-slot-modal')).not.toBeInTheDocument();
  });

  it('preselects the printer it was opened for and submits with the default timeout', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(
      <AssignNextSlotModal isOpen={true} onClose={onClose} spool={SPOOL as never} defaultPrinterId={2} onCreated={onCreated} />
    );

    const printerSelect = await screen.findByLabelText('Printer');
    await waitFor(() => expect(printerSelect).toHaveValue('2'));

    await user.click(screen.getByRole('button', { name: 'Wait for next slot' }));

    await waitFor(() => {
      expect(api.createPendingSlotAssignment).toHaveBeenCalledWith({
        spool_id: 42,
        printer_id: 2,
        timeout_seconds: 1800,
      });
    });
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('lets the user pick a printer and a longer wait', async () => {
    const user = userEvent.setup();
    render(<AssignNextSlotModal isOpen={true} onClose={vi.fn()} spool={SPOOL as never} />);

    const printerSelect = await screen.findByLabelText('Printer');
    // Two printers and no preselection: starts on "any printer".
    await waitFor(() => expect(printerSelect).toHaveValue('any'));
    await user.selectOptions(printerSelect, '1');
    await user.selectOptions(screen.getByLabelText('Wait at most'), '7200');

    await user.click(screen.getByRole('button', { name: 'Wait for next slot' }));

    await waitFor(() => {
      expect(api.createPendingSlotAssignment).toHaveBeenCalledWith({
        spool_id: 42,
        printer_id: 1,
        timeout_seconds: 7200,
      });
    });
  });

  it('defaults to the only printer there is', async () => {
    vi.mocked(api.getPrinters).mockResolvedValue([PRINTERS[0]] as never);
    render(<AssignNextSlotModal isOpen={true} onClose={vi.fn()} spool={SPOOL as never} />);

    const printerSelect = await screen.findByLabelText('Printer');
    await waitFor(() => expect(printerSelect).toHaveValue('1'));
  });
});

describe('NextSlotButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPrinters).mockResolvedValue(PRINTERS as never);
  });

  it('opens the modal without triggering the row click behind it', async () => {
    const user = userEvent.setup();
    const rowClick = vi.fn();
    render(
      <div onClick={rowClick}>
        <NextSlotButton spool={SPOOL as never} />
      </div>
    );

    await user.click(screen.getByRole('button', { name: /assign to the next loaded ams slot/i }));

    expect(await screen.findByTestId('next-slot-modal')).toBeInTheDocument();
    expect(screen.getByText('Assign to the next loaded slot')).toBeInTheDocument();
    expect(rowClick).not.toHaveBeenCalled();
  });
});
