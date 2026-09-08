/**
 * Pure helpers behind the TV / kiosk page (voron B10), kept out of the page
 * module so it exports only a component (react-refresh) and so the tests can
 * exercise them without rendering.
 *
 * The page has two sources for the same tiles: the ordinary printers API for a
 * signed-in browser, and the token-authenticated `/tv/printers` feed for a
 * screen with no login. Both are folded into `TvTileData` here so the tile
 * component has one shape to draw and the two modes cannot drift apart.
 */
import type { AMSTray, HMSError, Printer, PrinterStatus, TvFeedPrinter } from '../api/client';
import { getGlobalTrayId } from './amsHelpers';

export type TvState = 'printing' | 'paused' | 'finished' | 'error' | 'offline' | 'idle';

export const REFRESH_OPTIONS_SEC = [2, 5, 10, 15, 30, 60] as const;
export const DEFAULT_REFRESH_SEC = 5;

/** The spool fields a tile draws — the same four in both modes. */
export interface TvTray {
  tray_type: string | null;
  tray_sub_brands: string | null;
  tray_color: string | null;
  remain: number | null;
}

/** Everything one TV tile renders, independent of where it came from. */
export interface TvTileData {
  id: number;
  name: string;
  subtitle: string;
  state: TvState;
  connected: boolean;
  cameraRotation: number;
  hasCamera: boolean;
  /** Only while a job is on the bed; null otherwise (see buildTile). */
  jobName: string | null;
  progress: number | null;
  remaining: number | null;
  layerNum: number | null;
  totalLayers: number | null;
  tray: TvTray | null;
}

// The fields classifyTvState needs, shared by PrinterStatus and TvFeedPrinter.
interface TvStateSource {
  connected: boolean;
  state: string | null;
  hms_errors?: HMSError[] | null;
}

// Same buckets CameraTile paints its chip with, plus 'offline' — a chip on a
// video frame can lean on the "no signal" art, a tile with no video cannot.
export function classifyTvState(status: TvStateSource | undefined): TvState {
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
 *
 * The kiosk feed answers the same question server-side (see routes/tv.py), so
 * a wall token never receives the slots this walks over.
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

interface TileInput {
  id: number;
  name: string;
  model: string | null | undefined;
  location: string | null | undefined;
  provider: string | null | undefined;
  externalCameraEnabled: boolean | null | undefined;
  cameraRotation: number | null | undefined;
  source: TvStateSource | undefined;
  currentPrint: string | null | undefined;
  subtaskName: string | null | undefined;
  gcodeFile: string | null | undefined;
  progress: number | null | undefined;
  remainingTime: number | null | undefined;
  layerNum: number | null | undefined;
  totalLayers: number | null | undefined;
  tray: TvTray | null;
}

function buildTile(input: TileInput): TvTileData {
  const state = classifyTvState(input.source);
  const active = state === 'printing' || state === 'paused';
  return {
    id: input.id,
    name: input.name,
    subtitle: [input.model, input.location].filter(Boolean).join(' · '),
    state,
    connected: input.source?.connected ?? false,
    cameraRotation: input.cameraRotation ?? 0,
    // Bambu printers all carry a camera; a Klipper printer only has one when an
    // external camera is configured. A frame that can only ever say "no signal"
    // is left out rather than shown broken.
    hasCamera: input.provider !== 'klipper' || Boolean(input.externalCameraEnabled),
    // Only while a job is on the bed. After FINISH the printer keeps reporting
    // the last file and 100%, and a tile that says "Finished" next to a full
    // progress bar for hours reads as still running.
    jobName: active ? input.currentPrint || input.subtaskName || input.gcodeFile || null : null,
    progress: active && input.progress != null ? Math.round(input.progress) : null,
    remaining:
      active && input.remainingTime != null && input.remainingTime > 0 ? input.remainingTime : null,
    layerNum: active ? (input.layerNum ?? null) : null,
    totalLayers: active ? (input.totalLayers ?? null) : null,
    tray: input.tray,
  };
}

/** Signed-in mode: the printers API plus one status per printer. */
export function tileFromStatus(printer: Printer, status: PrinterStatus | undefined): TvTileData {
  const tray = resolveActiveTray(status);
  return buildTile({
    id: printer.id,
    name: printer.name,
    model: printer.model,
    location: printer.location,
    provider: printer.provider,
    externalCameraEnabled: printer.external_camera_enabled,
    cameraRotation: printer.camera_rotation,
    source: status,
    currentPrint: status?.current_print,
    subtaskName: status?.subtask_name,
    gcodeFile: status?.gcode_file,
    progress: status?.progress,
    remainingTime: status?.remaining_time,
    layerNum: status?.layer_num,
    totalLayers: status?.total_layers,
    tray: tray
      ? {
          tray_type: tray.tray_type ?? null,
          tray_sub_brands: tray.tray_sub_brands ?? null,
          tray_color: tray.tray_color ?? null,
          remain: tray.remain ?? null,
        }
      : null,
  });
}

/** Kiosk mode: one row of the token-authenticated /tv/printers feed. */
export function tileFromFeed(entry: TvFeedPrinter): TvTileData {
  return buildTile({
    id: entry.id,
    name: entry.name,
    model: entry.model,
    location: entry.location,
    provider: entry.provider,
    externalCameraEnabled: entry.external_camera_enabled,
    cameraRotation: entry.camera_rotation,
    source: entry,
    currentPrint: entry.current_print,
    subtaskName: entry.subtask_name,
    gcodeFile: entry.gcode_file,
    progress: entry.progress,
    remainingTime: entry.remaining_time,
    layerNum: entry.layer_num,
    totalLayers: entry.total_layers,
    tray: entry.tray,
  });
}
