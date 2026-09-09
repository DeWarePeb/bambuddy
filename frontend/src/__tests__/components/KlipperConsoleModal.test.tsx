import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { KlipperConsoleModal } from '../../components/KlipperConsoleModal';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getKlipperMacros: vi.fn(),
    getKlipperConsole: vi.fn(),
    sendKlipperGcode: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const defaultProps = {
  printerId: 1,
  printerName: 'Voron 2.4',
  isPrinting: false,
  canControl: true,
  onClose: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getKlipperMacros).mockResolvedValue({
    macros: [
      { name: 'PRINT_START', description: 'start a print' },
      { name: 'Z_TILT_ADJUST', description: '' },
    ],
  });
  vi.mocked(api.getKlipperConsole).mockResolvedValue({
    entries: [{ message: 'G28', time: 1, type: 'command' }],
  });
  vi.mocked(api.sendKlipperGcode).mockResolvedValue({ status: 'sent', script: 'X' });
});

describe('KlipperConsoleModal', () => {
  it('sends a macro under the name Klipper answers to', async () => {
    render(<KlipperConsoleModal {...defaultProps} />);

    const macro = await screen.findByRole('button', { name: 'Z_TILT_ADJUST' });
    await userEvent.click(macro);

    await waitFor(() => {
      expect(api.sendKlipperGcode).toHaveBeenCalledWith(1, 'Z_TILT_ADJUST', false);
    });
  });

  it('shows what the printer logged, not what this tab typed', async () => {
    render(<KlipperConsoleModal {...defaultProps} />);
    // The log is Moonraker's own G-code store, so a command from Mainsail is
    // on screen before anything has been sent from here.
    expect(await screen.findByText('G28')).toBeInTheDocument();
  });

  it('will not send during a print until the confirmation is ticked', async () => {
    render(<KlipperConsoleModal {...defaultProps} isPrinting />);

    const macro = await screen.findByRole('button', { name: 'PRINT_START' });
    expect(macro).toBeDisabled();
    await userEvent.click(macro);
    expect(api.sendKlipperGcode).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'PRINT_START' }));

    // Confirmed, and the flag rides along — the API refuses the send without
    // it, so a UI that ticked the box locally and sent false would 409.
    await waitFor(() => {
      expect(api.sendKlipperGcode).toHaveBeenCalledWith(1, 'PRINT_START', true);
    });
  });

  it('does not offer to send anything without control permission', async () => {
    render(<KlipperConsoleModal {...defaultProps} canControl={false} />);

    const macro = await screen.findByRole('button', { name: 'PRINT_START' });
    expect(macro).toBeDisabled();
    expect(screen.getByPlaceholderText(/permission/i)).toBeDisabled();
  });
});
