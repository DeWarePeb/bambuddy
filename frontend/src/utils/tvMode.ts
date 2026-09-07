/**
 * Pure helpers behind the TV / kiosk page (voron B10), kept out of the page
 * module so it exports only a component (react-refresh) and so the tests can
 * exercise them without rendering.
 */
import type { AMSTray, PrinterStatus } from '../api/client';
import { getGlobalTrayId } from './amsHelpers';

export type TvState = 'printing' | 'paused' | 'finished' | 'error' | 'offline' | 'idle';

export const REFRESH_OPTIONS_SEC = [2, 5, 10, 15, 30, 60] as const;
export const DEFAULT_REFRESH_SEC = 5;

// Same buckets CameraTile paints its chip with, plus 'offline' — a chip on a
// video frame can lean on the "no signal" art, a tile with no video cannot.
export function classifyTvState(status: PrinterStatus | undefined): TvState {
  if (!status || !status.connected) return 'offline';
  if ((status.hms_errors?.length ?? 0) > 0) return 'error';
  switch (status.state) {
    case 'RUNNING':
      return 'printing';
    case 'PAUSE':
      return 'paused';
    case 'FINISH':
    case 'FAILED':
      return 'finished';
    default:
      return 'idle';
  }
}

/**
 * The tray feeding the hotend right now, or null when nothing is loaded.
 *
 * ``tray_now`` is a global tray id: ``ams*4+slot`` for a regular AMS, the
 * unit id itself (128+) for an AMS-HT, 254 for the external spool, 255 for
 * "nothing loaded". A Klipper printer in this fork has no AMS and reports no
 * ``tray_now``, so it resolves to null and the tile shows no spool block at
 * all — an empty AMS graphic on a printer that cannot have one is noise.
 */
export function resolveActiveTray(status: PrinterStatus | undefined): AMSTray | null {
  if (!status) return null;
  const trayNow = status.tray_now;
  if (trayNow == null || trayNow === 255) return null;
  let tray: AMSTray | undefined;
  if (trayNow >= 254) {
    tray = status.vt_tray?.[trayNow - 254] ?? status.vt_tray?.[0];
  } else {
    for (const unit of status.ams ?? []) {
      tray = unit.tray?.find((t) => getGlobalTrayId(unit.id, t.id, false) === trayNow);
      if (tray) break;
    }
  }
  // A loaded-but-empty slot is not a spool.
  return tray && tray.tray_type ? tray : null;
}
