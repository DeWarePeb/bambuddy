/**
 * Farm command center (voron B11).
 *
 * One screen for the whole operation: fleet tiles bucketed by group, active
 * projects with what is running for them, an alert roll-up, and the shortcuts
 * out to inventory and maintenance.
 *
 * It composes endpoints that already exist — printers, statuses, queue,
 * projects, spools, maintenance overview — and adds exactly one of its own,
 * /printer-fleet-groups. All arithmetic lives in utils/farmFleet.ts so it can
 * be tested without rendering.
 *
 * Ported from vmhomelab/printbuddy's FarmCommandCenterPage, rewritten for
 * Bambuddy 1.2.5.x: translated instead of hardcoded English, "TV mode" points
 * at this fork's /tv rather than Printbuddy's /farm-monitor, and the alert
 * builder returns i18n keys rather than sentences.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  ChevronRight,
  Layers,
  Monitor,
  Pause,
  PackageOpen,
  Plus,
  Printer as PrinterIcon,
  Search,
  Wrench,
  X,
} from 'lucide-react';
import {
  api,
  type Printer,
  type PrinterFleetGroup,
  type PrinterStatus,
} from '../api/client';
import { filterKnownHMSErrors } from '../components/HMSErrorModal';
import {
  buildCommandCenterAlerts,
  classifyFleetState,
  clampPercent,
  completedToday,
  countStates,
  filterFleetGroups,
  fleetUtilization,
  groupFleet,
  lowSpoolCount,
  maintenanceAttentionCount,
  projectProgress,
  type CommandCenterAlert,
  type FleetEntry,
  type FleetState,
} from '../utils/farmFleet';

const REFRESH_MS = 15_000;

type FarmEntry = FleetEntry<PrinterStatus> & { printer: Printer };

function StatCard({
  icon: Icon,
  value,
  label,
  sub,
  tone,
  onClick,
}: {
  icon: typeof PrinterIcon;
  value: number | string;
  label: string;
  sub: string;
  tone: 'blue' | 'green' | 'gray' | 'amber' | 'purple';
  onClick?: () => void;
}) {
  const tones = {
    blue: 'bg-blue-500/15 text-blue-300',
    green: 'bg-emerald-500/15 text-emerald-300',
    gray: 'bg-slate-500/15 text-slate-300',
    amber: 'bg-amber-500/15 text-amber-300',
    purple: 'bg-purple-500/15 text-purple-300',
  };
  const content = (
    <>
      <div className="flex items-center gap-4">
        <div className={`flex h-14 w-14 items-center justify-center rounded-full ${tones[tone]}`}>
          <Icon className="h-7 w-7" />
        </div>
        <div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold text-white">{value}</span>
            <span className="text-xs font-semibold uppercase tracking-[0.2em] text-bambu-gray-light">
              {label}
            </span>
          </div>
          <div className="mt-1 text-sm font-semibold text-bambu-gray-light">{sub}</div>
        </div>
      </div>
      {onClick && (
        <ChevronRight className="absolute right-4 top-4 h-4 w-4 text-bambu-gray transition-transform group-hover:translate-x-0.5 group-hover:text-white" />
      )}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="group relative block w-full rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 text-left shadow-[var(--card-shadow)] transition hover:border-blue-500 hover:bg-bambu-dark-secondary focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        {content}
      </button>
    );
  }

  return (
    <div className="relative rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 shadow-[var(--card-shadow)]">
      {content}
    </div>
  );
}

function FleetTile({ entry }: { entry: FarmEntry }) {
  const { t } = useTranslation();
  const state = classifyFleetState(entry.status, entry.hasKnownHmsErrors);
  const progress = clampPercent(entry.status?.progress);
  const classes: Record<FleetState, string> = {
    printing: 'border-blue-500/70 bg-blue-500/15 text-blue-200',
    paused: 'border-emerald-500/70 bg-emerald-500/15 text-emerald-200',
    idle: 'border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-gray-light',
    alert: 'border-red-500/70 bg-red-500/15 text-red-200',
    offline: 'border-slate-700 bg-bambu-dark-secondary/70 text-bambu-gray',
  };
  return (
    <div className={`min-h-20 rounded-lg border p-3 ${classes[state]}`} data-testid="fleet-tile">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-white">{entry.printer.name}</div>
          <div className="mt-1 text-xs">{t(`farm.state.${state}`)}</div>
        </div>
        <span className="text-xs text-bambu-gray-light">
          {progress !== null ? `${progress}%` : '—'}
        </span>
      </div>
      {state === 'printing' && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-bambu-dark-tertiary">
          <div className="h-full rounded-full bg-blue-400" style={{ width: `${progress ?? 0}%` }} />
        </div>
      )}
    </div>
  );
}

function AlertsDialog({
  alerts,
  onClose,
}: {
  alerts: CommandCenterAlert[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toneClasses = {
    red: 'border-red-500/40 bg-red-500/10 text-red-200',
    amber: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
  };
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="farm-alerts-title"
    >
      <div className="w-full max-w-2xl rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="farm-alerts-title" className="text-xl font-bold text-white">
              {t('farm.alerts.title')}
            </h2>
            <p className="text-sm text-bambu-gray-light">{t('farm.alerts.subtitle')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close')}
            className="rounded-lg p-2 text-bambu-gray-light hover:bg-bambu-dark hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {alerts.length === 0 ? (
            <div className="rounded-xl border border-bambu-dark-tertiary bg-bambu-dark p-6 text-center text-bambu-gray-light">
              {t('farm.alerts.empty')}
            </div>
          ) : (
            alerts.map((alert) => (
              <div key={alert.id} className={`rounded-xl border p-4 ${toneClasses[alert.tone]}`}>
                <div className="font-bold text-white">{alert.title}</div>
                <div className="mt-1 text-sm">{t(alert.detailKey, alert.detailValues)}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function PrinterGroupsDialog({
  printers,
  groups,
  onSave,
  onClose,
}: {
  printers: Printer[];
  groups: PrinterFleetGroup[];
  onSave: (name: string, printerIds: number[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [selectedPrinterIds, setSelectedPrinterIds] = useState<number[]>([]);

  const togglePrinter = (printerId: number) => {
    setSelectedPrinterIds((current) =>
      current.includes(printerId)
        ? current.filter((id) => id !== printerId)
        : [...current, printerId],
    );
  };

  const saveGroup = () => {
    const trimmed = name.trim();
    if (!trimmed || selectedPrinterIds.length === 0) return;
    onSave(trimmed, selectedPrinterIds);
    setName('');
    setSelectedPrinterIds([]);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="farm-groups-title"
    >
      <div className="w-full max-w-2xl rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 id="farm-groups-title" className="text-xl font-bold text-white">
              {t('farm.groups.title')}
            </h2>
            <p className="text-sm text-bambu-gray-light">{t('farm.groups.subtitle')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close')}
            className="rounded-lg p-2 text-bambu-gray-light hover:bg-bambu-dark hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <label className="mb-4 block text-sm font-semibold text-bambu-gray-light">
          {t('farm.groups.nameLabel')}
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-2 w-full rounded-lg border border-bambu-dark-tertiary bg-bambu-dark px-3 py-2 text-white focus:border-blue-500 focus:outline-none"
          />
        </label>
        <div className="mb-4 grid gap-2 sm:grid-cols-2">
          {printers.map((printer) => (
            <label
              key={printer.id}
              className="flex items-center gap-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3 text-sm text-white"
            >
              <input
                type="checkbox"
                checked={selectedPrinterIds.includes(printer.id)}
                onChange={() => togglePrinter(printer.id)}
              />
              <span>{printer.name}</span>
              <span className="ml-auto text-xs text-bambu-gray-light">
                {printer.location || t('farm.ungrouped')}
              </span>
            </label>
          ))}
        </div>
        {groups.length > 0 && (
          <div className="mb-4 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3 text-sm text-bambu-gray-light">
            <div className="mb-2 font-semibold text-white">{t('farm.groups.current')}</div>
            {groups.map((group) => (
              <div key={group.id}>
                {group.name}: {t('farm.groups.printerCount', { count: group.printer_ids.length })}
              </div>
            ))}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-bambu-dark-tertiary px-4 py-2 text-sm font-semibold text-bambu-gray-light hover:text-white"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={saveGroup}
            disabled={!name.trim() || selectedPrinterIds.length === 0}
            className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('farm.groups.save')}
          </button>
        </div>
      </div>
    </div>
  );
}

export function FarmCommandCenterPage() {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => new Date());
  const [search, setSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState('all');
  const [showAlerts, setShowAlerts] = useState(false);
  const [showGroups, setShowGroups] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    refetchInterval: REFRESH_MS,
  });
  const { data: printerGroups = [] } = useQuery({
    queryKey: ['printer-fleet-groups'],
    queryFn: api.getPrinterFleetGroups,
    refetchInterval: REFRESH_MS,
    retry: false,
  });
  const { data: queue = [] } = useQuery({
    queryKey: ['queue', 'farm'],
    queryFn: () => api.getQueue(),
    refetchInterval: REFRESH_MS,
  });
  const { data: activeProjects = [] } = useQuery({
    queryKey: ['projects', 'active', 'farm'],
    queryFn: () => api.getProjects('active'),
    refetchInterval: REFRESH_MS,
    retry: false,
  });
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    staleTime: 60_000,
  });
  const { data: spools = [] } = useQuery({
    queryKey: ['inventory-spools', 'farm'],
    queryFn: () => api.getSpools(false),
    refetchInterval: REFRESH_MS,
    retry: false,
  });
  const { data: maintenanceOverview = [] } = useQuery({
    queryKey: ['maintenance-overview', 'farm'],
    queryFn: api.getMaintenanceOverview,
    refetchInterval: REFRESH_MS,
    retry: false,
  });

  const statusQueries = useQueries({
    queries: printers.map((printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: REFRESH_MS,
      enabled: printer.is_active !== false,
    })),
  });

  const createPrinterGroup = useMutation({
    mutationFn: (payload: { name: string; printerIds: number[] }) =>
      api.createPrinterFleetGroup({
        name: payload.name,
        printer_ids: payload.printerIds,
        sort_order: printerGroups.length,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['printer-fleet-groups'] }),
  });

  const fleet = useMemo<FarmEntry[]>(
    () =>
      printers.map((printer, index) => {
        const status = statusQueries[index]?.data;
        return {
          printer,
          status,
          hasKnownHmsErrors: status?.hms_errors
            ? filterKnownHMSErrors(status.hms_errors).length > 0
            : false,
        };
      }),
    [printers, statusQueries],
  );

  const threshold = settings?.low_stock_threshold ?? 20;
  const stateCounts = useMemo(() => countStates(fleet), [fleet]);
  const utilization = useMemo(() => fleetUtilization(fleet), [fleet]);
  const groupedFleet = useMemo(() => groupFleet(fleet, printerGroups), [fleet, printerGroups]);
  const visibleGroups = useMemo(
    () => filterFleetGroups(groupedFleet, search, groupFilter),
    [groupedFleet, search, groupFilter],
  );
  const lowStock = useMemo(() => lowSpoolCount(spools, threshold), [spools, threshold]);
  const maintenanceDue = useMemo(
    () => maintenanceAttentionCount(maintenanceOverview),
    [maintenanceOverview],
  );
  const alerts = useMemo(
    () => buildCommandCenterAlerts(fleet, spools, maintenanceOverview, threshold),
    [fleet, spools, maintenanceOverview, threshold],
  );
  const alertCount = alerts.length;
  const todayParts = useMemo(() => completedToday(queue, now), [queue, now]);
  const online = fleet.length - stateCounts.offline;
  const visibleProjects = activeProjects.slice(0, 4);

  const share = (count: number) =>
    fleet.length ? Math.round((count / fleet.length) * 100) : 0;

  return (
    <div className="min-h-full bg-bambu-dark p-4 text-white xl:p-6">
      <div className="mx-auto max-w-[1700px] rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/90 p-5 shadow-[var(--card-shadow)]">
        <header className="mb-5 flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="border-l-4 border-blue-500 pl-4">
            <h1 className="text-2xl font-bold tracking-tight text-white">{t('farm.title')}</h1>
            <p className="text-sm text-bambu-gray-light">{t('farm.subtitle')}</p>
          </div>
          <div className="flex flex-col gap-3 xl:items-end">
            <div className="flex items-center gap-4">
              <span className="text-xs font-semibold uppercase tracking-[0.22em] text-bambu-gray">
                {t('farm.utilization')}
              </span>
              <span className="text-3xl font-bold text-blue-400">{utilization}%</span>
              <div className="h-2 w-28 overflow-hidden rounded-full bg-bambu-dark-tertiary">
                <div
                  className="h-full rounded-full bg-blue-500"
                  style={{ width: `${utilization}%` }}
                />
              </div>
              <span className="text-xs text-bambu-gray-light">
                ({stateCounts.printing} / {fleet.length})
              </span>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3">
              <div className="text-right">
                <div className="font-mono text-3xl font-bold tracking-wider text-blue-300">
                  {now.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                  })}
                </div>
                <div className="text-xs text-bambu-gray-light">
                  {now.toLocaleDateString([], {
                    weekday: 'short',
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </div>
              </div>
              <Link
                to="/tv"
                className="inline-flex items-center gap-2 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark px-3 py-2 text-sm font-semibold text-bambu-gray-light hover:border-blue-500 hover:text-white"
              >
                <Monitor className="h-4 w-4" /> {t('farm.tvMode')}
              </Link>
            </div>
          </div>
        </header>

        <section className="mb-5 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <StatCard
            icon={Boxes}
            value={stateCounts.printing}
            label={t('farm.stats.printing')}
            sub={t('farm.stats.ofFleet', { percent: utilization })}
            tone="blue"
          />
          <StatCard
            icon={PrinterIcon}
            value={stateCounts.idle}
            label={t('farm.stats.idle')}
            sub={t('farm.stats.ofFleet', { percent: share(stateCounts.idle) })}
            tone="gray"
          />
          <StatCard
            icon={Pause}
            value={stateCounts.paused}
            label={t('farm.stats.paused')}
            sub={t('farm.stats.ofFleet', { percent: share(stateCounts.paused) })}
            tone="green"
          />
          <StatCard
            icon={AlertTriangle}
            value={alertCount}
            label={t('farm.stats.alerts')}
            sub={t('farm.stats.alertsSub')}
            tone="amber"
            onClick={() => setShowAlerts(true)}
          />
          <StatCard
            icon={Layers}
            value={todayParts}
            label={t('farm.stats.partsToday')}
            sub={t('farm.stats.queueTracked', { count: queue.length })}
            tone="purple"
          />
        </section>

        <section className="mb-5 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark/50 p-4">
          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-[0.22em] text-bambu-gray-light">
              {t('farm.fleetStatus')}
            </h2>
            <div className="flex flex-col gap-2 sm:flex-row">
              <label className="relative block">
                <span className="sr-only">{t('farm.searchPrinters')}</span>
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-bambu-gray" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder={t('farm.searchPrinters')}
                  className="w-full rounded-lg border border-bambu-dark-tertiary bg-bambu-dark py-2 pl-9 pr-3 text-sm text-white placeholder-bambu-gray focus:border-blue-500 focus:outline-none sm:w-64"
                />
              </label>
              <select
                value={groupFilter}
                onChange={(event) => setGroupFilter(event.target.value)}
                aria-label={t('farm.groupFilter')}
                className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                <option value="all">{t('farm.allGroups')}</option>
                {groupedFleet.map((group) => (
                  <option key={group.name} value={group.name}>
                    {group.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setShowGroups(true)}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-blue-500/60 bg-blue-500/10 px-3 py-2 text-sm font-semibold text-blue-300 transition hover:bg-blue-500/20 hover:text-white"
              >
                <Plus className="h-4 w-4" /> {t('farm.createGroup')}
              </button>
            </div>
          </div>

          <div className="space-y-3">
            {visibleGroups.map((group) => (
              <div key={group.name} className="grid gap-3 xl:grid-cols-[220px_1fr]">
                <div className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary/70 p-4">
                  <div className="flex items-center justify-between">
                    <div className="font-bold text-white">{group.name}</div>
                    <span className="rounded-full bg-bambu-dark-tertiary px-2 py-0.5 text-xs font-bold text-bambu-gray-light">
                      {group.items.length}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-sm text-bambu-gray-light">
                    <span className="h-2 w-2 rounded-full bg-emerald-400" />
                    {t('farm.onlineCount', {
                      count: group.items.filter(
                        (item) =>
                          classifyFleetState(item.status, item.hasKnownHmsErrors) !== 'offline',
                      ).length,
                    })}
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-8">
                  {group.items.map((item) => (
                    <FleetTile key={item.printer.id} entry={item} />
                  ))}
                </div>
              </div>
            ))}
            {visibleGroups.length === 0 && (
              <div className="rounded-xl border border-dashed border-bambu-dark-tertiary p-8 text-center text-bambu-gray-light">
                {t('farm.noPrintersMatch')}
              </div>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-bambu-dark-tertiary pt-4 text-sm text-bambu-gray-light">
            <div className="flex flex-wrap gap-4">
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded bg-blue-500" /> {t('farm.state.printing')}
              </span>
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded bg-emerald-500" /> {t('farm.state.paused')}
              </span>
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded bg-slate-400" /> {t('farm.state.idle')}
              </span>
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded bg-red-500" /> {t('farm.state.alert')}
              </span>
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 rounded bg-slate-700" /> {t('farm.state.offline')}
              </span>
            </div>
            <div className="flex flex-wrap gap-6">
              <span>{t('farm.totalCount', { count: fleet.length })}</span>
              <span className="text-emerald-300">{t('farm.onlineCount', { count: online })}</span>
              <span>{t('farm.offlineCount', { count: stateCounts.offline })}</span>
            </div>
          </div>
        </section>

        <section className="mb-5 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark/50 p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-[0.22em] text-bambu-gray-light">
              {t('farm.activeProjects')}
            </h2>
            <Link
              to="/projects"
              className="inline-flex items-center gap-1 text-sm font-semibold text-blue-400 hover:text-blue-300"
            >
              {t('farm.viewAllProjects')} <ChevronRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            {visibleProjects.map((project) => {
              const progress = projectProgress(project);
              return (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="block rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 transition hover:border-blue-500"
                >
                  <div className="flex gap-4">
                    <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-bambu-dark-tertiary bg-bambu-dark">
                      {project.cover_image_filename ? (
                        <img
                          src={api.getProjectCoverImageUrl(project.id)}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <PackageOpen className="h-9 w-9 text-bambu-gray" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-base font-bold text-white">{project.name}</h3>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold text-bambu-gray-light">
                        {project.queue_count > 0 && (
                          <span className="rounded-lg bg-bambu-dark px-2 py-1">
                            {t('farm.project.queued', { count: project.queue_count })}
                          </span>
                        )}
                        {project.failed_count > 0 && (
                          <span className="rounded-lg bg-red-500/15 px-2 py-1 text-red-300">
                            {t('farm.project.failed', { count: project.failed_count })}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="w-20 shrink-0 text-right">
                      <div className="text-2xl font-bold text-blue-400">
                        {progress !== null ? `${progress}%` : '—'}
                      </div>
                      <div className="text-xs text-bambu-gray-light">{t('farm.project.complete')}</div>
                    </div>
                  </div>
                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-bambu-dark-tertiary">
                    <div
                      className="h-full rounded-full bg-blue-500"
                      style={{ width: `${progress ?? 0}%` }}
                    />
                  </div>
                </Link>
              );
            })}
            {visibleProjects.length === 0 && (
              <div className="rounded-xl border border-dashed border-bambu-dark-tertiary p-8 text-center text-bambu-gray-light xl:col-span-2">
                {t('farm.noActiveProjects')}
              </div>
            )}
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-3">
          <button
            type="button"
            onClick={() => setShowAlerts(true)}
            className="group flex items-center gap-4 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 text-left transition hover:border-blue-500"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300">
              <CheckCircle2 className="h-7 w-7" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-bold text-white">
                {alertCount === 0
                  ? t('farm.allOperational')
                  : t('farm.needAttention', { count: alertCount })}
              </div>
              <div className="text-sm text-bambu-gray-light">
                {alertCount === 0 ? t('farm.noIssues') : t('farm.openAlerts')}
              </div>
            </div>
            <ChevronRight className="h-5 w-5 text-bambu-gray group-hover:text-white" />
          </button>
          <Link
            to="/inventory"
            className="group flex items-center gap-4 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 transition hover:border-blue-500"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-bambu-dark-tertiary text-bambu-gray-light">
              <PackageOpen className="h-7 w-7" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-bold text-white">{t('farm.filamentStock')}</div>
              <div className="text-sm text-bambu-gray-light">
                {t('farm.spoolsLow', { count: lowStock })}
              </div>
            </div>
            <ChevronRight className="h-5 w-5 text-bambu-gray group-hover:text-white" />
          </Link>
          <Link
            to="/maintenance"
            className="group flex items-center gap-4 rounded-2xl border border-bambu-dark-tertiary bg-bambu-dark-secondary/80 p-4 transition hover:border-blue-500"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-500/15 text-red-300">
              <Wrench className="h-7 w-7" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-bold text-white">{t('farm.maintenance')}</div>
              <div className="text-sm text-bambu-gray-light">
                {t('farm.maintenanceDue', { count: maintenanceDue })}
              </div>
            </div>
            <ChevronRight className="h-5 w-5 text-bambu-gray group-hover:text-white" />
          </Link>
        </section>
      </div>
      {showAlerts && <AlertsDialog alerts={alerts} onClose={() => setShowAlerts(false)} />}
      {showGroups && (
        <PrinterGroupsDialog
          printers={printers}
          groups={printerGroups}
          onSave={(name, printerIds) => createPrinterGroup.mutate({ name, printerIds })}
          onClose={() => setShowGroups(false)}
        />
      )}
    </div>
  );
}
