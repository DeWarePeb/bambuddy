/**
 * TV / kiosk mode at ``/tv`` (voron B10) — the status-first sibling of the
 * Cam Wall.
 *
 * The Cam Wall is a wall of video with a chip in the corner; this page is the
 * other way round. One tile per printer carries the state, the file on the
 * bed, progress, layer, time left and ETA, the spool that is feeding the
 * hotend, and a small camera frame when the printer has one. A strip above the
 * grid counts printers by state and a clock sits in the corner, so a glance at
 * the shop TV answers "what is running, what needs me, what time is it".
 *
 * Two ways in, exactly like the Cam Wall:
 *
 * - **Signed in.** The ordinary printers API plus one status per printer, from
 *   the same ``['printerStatus', id]`` cache the printer cards fill, so a hop
 *   between pages does not start from blank tiles.
 * - **``?token=<tv token>``.** For a screen with no login — the shop TV, a Pi
 *   in kiosk mode. One request to ``/tv/printers`` backs the whole wall and
 *   the same token authenticates the camera snapshots. That feed sits behind
 *   its own ``tv`` scope rather than ``camwall`` because these tiles name the
 *   file on the bed and the spool feeding it, which a Cam Wall token is
 *   trusted never to show.
 *
 * Rendered outside the app layout: no sidebar, no WebSocket provider —
 * statuses are polled at the interval chosen in the header, the same trade the
 * Cam Wall page makes.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  ArrowLeft,
  Clock3,
  Disc3,
  Layers,
  Printer as PrinterIcon,
  Timer,
  Tv,
} from 'lucide-react';
import { api, setStreamToken } from '../api/client';
import { CameraTile } from '../components/CameraTile';
import { useAuth } from '../contexts/AuthContext';
import { formatDuration } from '../utils/date';
import { normalizeColor } from '../utils/amsHelpers';
import {
  DEFAULT_REFRESH_SEC,
  REFRESH_OPTIONS_SEC,
  tileFromFeed,
  tileFromStatus,
  type TvState,
  type TvTileData,
} from '../utils/tvMode';

// Counted in the status strip, in the order the strip shows them. 'finished'
// is a tile label only: a printer that finished is free, so it counts as idle.
const STRIP_STATES = ['printing', 'paused', 'idle', 'error', 'offline'] as const;
type StripState = (typeof STRIP_STATES)[number];

const REFRESH_STORAGE_KEY = 'tvRefreshSec';
const CAMERAS_STORAGE_KEY = 'tvCameras';

function clampRefresh(raw: string | null | undefined): number {
  const n = parseInt(raw ?? '', 10);
  if (!Number.isFinite(n)) return DEFAULT_REFRESH_SEC;
  // Snap to the nearest offered step so the <select> always has a matching option.
  return REFRESH_OPTIONS_SEC.reduce((best, opt) =>
    Math.abs(opt - n) < Math.abs(best - n) ? opt : best,
  );
}

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A kiosk browser with storage disabled still gets a working page.
  }
}

function formatEta(remainingMin: number | null | undefined): string | null {
  if (remainingMin == null || remainingMin <= 0) return null;
  const eta = new Date(Date.now() + remainingMin * 60_000);
  return eta.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

const STATE_CHIP_CLASS: Record<TvState, string> = {
  printing: 'bg-bambu-green/85 text-black',
  paused: 'bg-amber-500/85 text-black',
  finished: 'bg-sky-500/80 text-white',
  error: 'bg-red-500/85 text-white',
  offline: 'bg-bambu-dark-tertiary text-bambu-gray',
  idle: 'bg-bambu-dark-tertiary/80 text-bambu-gray',
};

const STATE_DOT_CLASS: Record<StripState, string> = {
  printing: 'bg-bambu-green',
  paused: 'bg-amber-400',
  idle: 'bg-bambu-gray',
  error: 'bg-red-500',
  offline: 'bg-bambu-dark-tertiary',
};

/** Ticks on its own so the seconds hand does not re-render every tile. */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="text-right" data-testid="tv-clock">
      <div className="font-mono text-2xl font-bold tracking-wider sm:text-3xl">
        {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </div>
      <div className="text-xs text-bambu-gray">
        {now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}
      </div>
    </div>
  );
}

function StatusStrip({ counts, total }: { counts: Record<StripState, number>; total: number }) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="tv-status-strip"
      className="flex flex-wrap items-center gap-2 rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary px-3 py-2 text-sm"
    >
      <span className="flex items-center gap-1.5 pr-2 font-semibold text-white">
        <PrinterIcon className="h-4 w-4 text-bambu-gray" aria-hidden="true" />
        {t('printers.tv.total', { count: total })}
      </span>
      {STRIP_STATES.map((state) => (
        <span
          key={state}
          data-testid={`tv-count-${state}`}
          className={`flex items-center gap-1.5 rounded-lg px-2 py-0.5 ${
            counts[state] > 0 ? 'text-white' : 'text-bambu-gray'
          }`}
        >
          <span className={`h-2.5 w-2.5 rounded-full ${STATE_DOT_CLASS[state]}`} aria-hidden="true" />
          <span className="font-bold tabular-nums">{counts[state]}</span>
          <span>{t(`printers.status.${state}`)}</span>
        </span>
      ))}
    </div>
  );
}

interface TvTileProps {
  tile: TvTileData;
  showCamera: boolean;
  refreshMs: number;
}

function TvTile({ tile, showCamera, refreshMs }: TvTileProps) {
  const { t } = useTranslation();
  const active = tile.state === 'printing' || tile.state === 'paused';
  const eta = formatEta(tile.remaining);
  const hasLayers = tile.layerNum != null && tile.totalLayers != null && tile.totalLayers > 0;

  return (
    <article
      data-testid={`tv-tile-${tile.id}`}
      data-state={tile.state}
      className="flex min-w-0 flex-col gap-3 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-3 sm:p-4"
    >
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-bold text-white sm:text-xl">{tile.name}</h2>
          {tile.subtitle && <p className="truncate text-xs text-bambu-gray">{tile.subtitle}</p>}
        </div>
        <span
          className={`flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${STATE_CHIP_CLASS[tile.state]}`}
        >
          {tile.state === 'error' && <AlertTriangle className="h-3 w-3" aria-hidden="true" />}
          {t(`printers.status.${tile.state}`)}
        </span>
      </div>

      {showCamera && tile.hasCamera && (
        <CameraTile
          printerId={tile.id}
          printerName={tile.name}
          cameraRotation={tile.cameraRotation}
          mode={tile.connected ? 'snapshot' : 'paused'}
          snapshotIntervalMs={refreshMs}
          connected={tile.connected}
          statusMode="off"
        />
      )}

      {active ? (
        <div className="space-y-2">
          <div className="flex min-w-0 items-end justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[0.65rem] font-semibold uppercase tracking-wide text-bambu-gray">
                {t('printers.tv.currentJob')}
              </div>
              <div className="truncate font-semibold text-white" title={tile.jobName ?? undefined}>
                {tile.jobName ?? t('printers.tv.unnamedJob')}
              </div>
            </div>
            <div className="text-2xl font-bold tabular-nums text-white">
              {tile.progress != null ? `${tile.progress}%` : '—'}
            </div>
          </div>
          <div
            className="h-2.5 overflow-hidden rounded-full bg-bambu-dark-tertiary"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={tile.progress ?? undefined}
            aria-label={t('printers.tv.progress')}
          >
            <div
              className={`h-full rounded-full ${tile.state === 'paused' ? 'bg-amber-400' : 'bg-bambu-green'}`}
              style={{ width: `${tile.progress ?? 0}%` }}
            />
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-bambu-gray">
            {hasLayers && (
              <span className="flex items-center gap-1">
                <Layers className="h-4 w-4" aria-hidden="true" />
                {t('printers.camWall.layer', { cur: tile.layerNum, total: tile.totalLayers })}
              </span>
            )}
            {tile.remaining != null && (
              <span className="flex items-center gap-1">
                <Timer className="h-4 w-4" aria-hidden="true" />
                {t('printers.camWall.timeLeft', { time: formatDuration(tile.remaining * 60) })}
              </span>
            )}
            {eta && (
              <span className="flex items-center gap-1">
                <Clock3 className="h-4 w-4" aria-hidden="true" />
                {t('printers.tv.eta', { time: eta })}
              </span>
            )}
          </div>
        </div>
      ) : (
        <div className="text-sm text-bambu-gray">{t(`printers.tv.idleHint.${tile.state}`)}</div>
      )}

      {tile.tray && (
        <div
          data-testid={`tv-spool-${tile.id}`}
          className="flex items-center gap-2 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark px-2 py-1.5 text-sm"
        >
          <Disc3 className="h-4 w-4 shrink-0 text-bambu-gray" aria-hidden="true" />
          <span
            className="h-4 w-4 shrink-0 rounded-full border border-bambu-dark-tertiary"
            style={{ backgroundColor: normalizeColor(tile.tray.tray_color) }}
            aria-hidden="true"
          />
          <span className="min-w-0 truncate text-white">
            {tile.tray.tray_sub_brands || tile.tray.tray_type}
          </span>
          {tile.tray.remain != null && tile.tray.remain >= 0 && (
            <span className="ml-auto shrink-0 tabular-nums text-bambu-gray">{tile.tray.remain}%</span>
          )}
        </div>
      )}
    </article>
  );
}

export function TvPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const { authEnabled, loading: authLoading, user, hasPermission } = useAuth();
  const [searchParams] = useSearchParams();

  const token = searchParams.get('token');
  const kiosk = token != null && token !== '';

  // URL wins, then what was last chosen on this screen, then the default —
  // a kiosk URL taped to the TV can pin its own cadence.
  const [refreshSec, setRefreshSec] = useState(() =>
    clampRefresh(searchParams.get('refresh') ?? readStored(REFRESH_STORAGE_KEY)),
  );
  const [showCameras, setShowCameras] = useState(() => {
    const raw = searchParams.get('cams') ?? readStored(CAMERAS_STORAGE_KEY);
    return raw == null ? true : raw !== '0' && raw !== 'false';
  });
  const refreshMs = refreshSec * 1000;

  // The snapshot <img> tags cannot send an Authorization header, so they carry
  // the token in the query string — the same withStreamToken() path CameraTile
  // uses on a signed-in wall. Safe against the app-wide stream-token sync:
  // that query is disabled while no user is signed in, which is the kiosk case.
  useEffect(() => {
    if (!kiosk) return;
    setStreamToken(token);
    return () => setStreamToken(null);
  }, [kiosk, token]);

  const kioskQuery = useQuery({
    queryKey: ['tv-printers', token],
    queryFn: () => api.getTvPrinters(token ?? undefined),
    enabled: kiosk,
    refetchInterval: refreshMs,
  });

  const signedIn = !kiosk && !authLoading && (!authEnabled || user !== null);
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: signedIn,
    refetchInterval: refreshMs,
  });
  const printers = useMemo(
    () => (printersQuery.data ?? []).filter((p) => p.is_active !== false),
    [printersQuery.data],
  );

  // Same ['printerStatus', id] cache the printer cards and the Cam Wall fill,
  // so a hop between pages does not start from blank tiles.
  const statusQueries = useQueries({
    queries: printers.map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
      enabled: signedIn,
      refetchInterval: refreshMs,
      staleTime: refreshMs,
    })),
  });
  const statuses = statusQueries.map((q) => q.data);

  // Both modes end up as the same tile list, so everything below draws once.
  const tiles: TvTileData[] = kiosk
    ? (kioskQuery.data ?? []).map(tileFromFeed)
    : printers.map((printer, i) => tileFromStatus(printer, statuses[i]));

  const counts: Record<StripState, number> = { printing: 0, paused: 0, idle: 0, error: 0, offline: 0 };
  for (const tile of tiles) {
    counts[tile.state === 'finished' ? 'idle' : tile.state] += 1;
  }

  if (!kiosk && authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bambu-dark text-bambu-gray">
        {t('common.loading')}
      </div>
    );
  }
  // No token and no session: this is just a normal page of the app.
  if (!kiosk && authEnabled && !user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  const query = kiosk ? kioskQuery : printersQuery;
  // A kiosk has no user to carry permissions; the token itself is the grant,
  // and it already reaches the snapshot endpoints it draws from.
  const cameraAllowed = kiosk || hasPermission('camera:view');

  return (
    <div className="min-h-screen bg-bambu-dark p-3 text-white sm:p-4">
      <header className="mb-3 flex flex-wrap items-center gap-3">
        {!kiosk && (
          <Link
            to="/"
            aria-label={t('printers.tv.backToApp')}
            title={t('printers.tv.backToApp')}
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-gray transition-colors hover:text-white"
          >
            <ArrowLeft className="h-5 w-5" aria-hidden="true" />
          </Link>
        )}
        <h1 className="flex items-center gap-2 text-xl font-bold sm:text-2xl">
          <Tv className="h-6 w-6 text-bambu-green" aria-hidden="true" />
          {t('printers.tv.title')}
        </h1>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          {cameraAllowed && (
            <label className="flex cursor-pointer items-center gap-1.5 text-sm text-bambu-gray">
              <input
                type="checkbox"
                className="accent-bambu-green"
                checked={showCameras}
                onChange={(e) => {
                  setShowCameras(e.target.checked);
                  writeStored(CAMERAS_STORAGE_KEY, e.target.checked ? '1' : '0');
                }}
              />
              {t('printers.tv.cameras')}
            </label>
          )}
          <label className="flex items-center gap-1.5 text-sm text-bambu-gray">
            <span>{t('printers.tv.refreshInterval')}</span>
            <select
              aria-label={t('printers.tv.refreshInterval')}
              value={refreshSec}
              onChange={(e) => {
                const next = clampRefresh(e.target.value);
                setRefreshSec(next);
                writeStored(REFRESH_STORAGE_KEY, String(next));
              }}
              className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary px-2 py-1 text-sm text-white"
            >
              {REFRESH_OPTIONS_SEC.map((sec) => (
                <option key={sec} value={sec}>
                  {t('printers.tv.everySeconds', { seconds: sec })}
                </option>
              ))}
            </select>
          </label>
          <Clock />
        </div>
      </header>

      <StatusStrip counts={counts} total={tiles.length} />

      {query.isError ? (
        <p className="mt-6 text-center text-sm text-red-400">
          {kiosk ? t('printers.tv.tokenRejected') : t('printers.camWall.page.loadFailed')}
        </p>
      ) : query.isSuccess && tiles.length === 0 ? (
        <p className="mt-6 text-center text-sm text-bambu-gray">{t('printers.camWall.noPrinters')}</p>
      ) : (
        <main
          data-testid="tv-grid"
          className="mt-3 grid gap-3 grid-cols-[repeat(auto-fit,minmax(min(100%,20rem),1fr))] sm:gap-4"
        >
          {tiles.map((tile) => (
            <TvTile
              key={tile.id}
              tile={tile}
              showCamera={cameraAllowed && showCameras}
              refreshMs={refreshMs}
            />
          ))}
        </main>
      )}
    </div>
  );
}

export default TvPage;
