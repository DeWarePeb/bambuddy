/**
 * Pure helpers for the farm command center (voron B11).
 *
 * Everything the page computes lives here so it can be tested without
 * rendering: fleet state buckets, group assignment, utilization, project
 * progress and the alert list.
 *
 * `classifyFleetState` deliberately mirrors `classifyPrinterStatus` in
 * PrintersPage.tsx rather than importing it — that function is module-private
 * there, and exporting it would mean editing the single file upstream changes
 * most, on every release, forever. The buckets must stay in step: FAILED with
 * no attached HMS error is a terminal state like FINISH, not an alert, and
 * only a *known* HMS error escalates to one. `PrintersPageBucketing.test.ts`
 * mirrors the same rules for the same reason.
 */

export type FleetState = 'printing' | 'paused' | 'idle' | 'alert' | 'offline';

export interface FleetStatusLike {
  connected?: boolean | null;
  state?: string | null;
  progress?: number | null;
  current_print?: string | null;
  gcode_file?: string | null;
}

export interface FleetPrinterLike {
  id: number;
  name: string;
  location?: string | null;
}

export interface FleetEntry<TStatus extends FleetStatusLike = FleetStatusLike> {
  printer: FleetPrinterLike;
  status?: TStatus;
  /** Result of filterKnownHMSErrors(...).length > 0, computed by the caller. */
  hasKnownHmsErrors?: boolean;
}

export const UNGROUPED = 'Ungrouped';

/**
 * Bucket a printer for the fleet grid.
 *
 * A printer that is not connected is offline before anything else is
 * considered — a stale status from a box that has gone away must not be
 * reported as printing.
 */
export function classifyFleetState(
  status: FleetStatusLike | undefined,
  hasKnownHmsErrors = false,
): FleetState {
  if (!status?.connected) return 'offline';
  if (hasKnownHmsErrors) return 'alert';
  switch (status.state) {
    case 'RUNNING':
      return 'printing';
    case 'PAUSE':
      return 'paused';
    // FINISH and FAILED are both terminal. FAILED covers user cancellation
    // too, so it only becomes an alert when an HMS code is actually attached,
    // which the early return above has already handled.
    case 'FINISH':
    case 'FAILED':
      return 'idle';
    default:
      return 'idle';
  }
}

export function clampPercent(value: number | null | undefined): number | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return Math.min(100, Math.max(0, Math.round(value)));
}

export function countStates(entries: FleetEntry[]): Record<FleetState, number> {
  return entries.reduce<Record<FleetState, number>>(
    (counts, entry) => {
      counts[classifyFleetState(entry.status, entry.hasKnownHmsErrors)] += 1;
      return counts;
    },
    { printing: 0, paused: 0, idle: 0, alert: 0, offline: 0 },
  );
}

/** Share of the fleet actively printing, 0 when there are no printers. */
export function fleetUtilization(entries: FleetEntry[]): number {
  if (entries.length === 0) return 0;
  return Math.round((countStates(entries).printing / entries.length) * 100);
}

export interface FleetGroupLike {
  name: string;
  printer_ids: number[];
}

/** printer id -> group name, for printers that are in a group. */
export function buildPrinterGroupMap(groups: FleetGroupLike[]): Map<number, string> {
  const map = new Map<number, string>();
  groups.forEach((group) => group.printer_ids.forEach((id) => map.set(id, group.name)));
  return map;
}

export interface FleetGroupBucket<T extends FleetEntry = FleetEntry> {
  name: string;
  items: T[];
}

/**
 * Bucket printers by fleet group, falling back to the printer's own location
 * and then to "Ungrouped", so every printer appears exactly once even with no
 * groups configured at all.
 */
export function groupFleet<T extends FleetEntry>(
  entries: T[],
  groups: FleetGroupLike[],
): FleetGroupBucket<T>[] {
  const groupNameById = buildPrinterGroupMap(groups);
  const buckets = new Map<string, T[]>();
  entries.forEach((entry) => {
    const name =
      groupNameById.get(entry.printer.id) || entry.printer.location?.trim() || UNGROUPED;
    if (!buckets.has(name)) buckets.set(name, []);
    buckets.get(name)!.push(entry);
  });
  return Array.from(buckets.entries()).map(([name, items]) => ({ name, items }));
}

/** Apply the search box and the group dropdown, dropping buckets left empty. */
export function filterFleetGroups<T extends FleetEntry>(
  buckets: FleetGroupBucket<T>[],
  search: string,
  groupFilter: string,
): FleetGroupBucket<T>[] {
  const needle = search.trim().toLowerCase();
  return buckets
    .filter((bucket) => groupFilter === 'all' || bucket.name === groupFilter)
    .map((bucket) => ({
      ...bucket,
      items: needle
        ? bucket.items.filter((item) => item.printer.name.toLowerCase().includes(needle))
        : bucket.items,
    }))
    .filter((bucket) => bucket.items.length > 0);
}

export interface ProjectProgressLike {
  progress_percent?: number | null;
  target_parts_count?: number | null;
  completed_count: number;
  target_count?: number | null;
  archive_count: number;
}

/**
 * Percent complete for a project, preferring the server's own number and
 * falling back to parts, then plates. Null when the project has no target to
 * measure against — the page shows a dash rather than a misleading 0 %.
 */
export function projectProgress(project: ProjectProgressLike): number | null {
  if (project.progress_percent !== null && project.progress_percent !== undefined) {
    return clampPercent(project.progress_percent);
  }
  if (project.target_parts_count && project.target_parts_count > 0) {
    return clampPercent((project.completed_count / project.target_parts_count) * 100);
  }
  if (project.target_count && project.target_count > 0) {
    return clampPercent((project.archive_count / project.target_count) * 100);
  }
  return null;
}

export interface QueueItemLike {
  status: string;
  completed_at?: string | null;
}

/** Queue items completed on the given calendar day, in the browser's zone. */
export function completedToday(queue: QueueItemLike[], now: Date): number {
  const today = now.toDateString();
  return queue.filter(
    (item) =>
      item.status === 'completed' &&
      item.completed_at &&
      new Date(item.completed_at).toDateString() === today,
  ).length;
}

export interface SpoolLike {
  id: number;
  material: string;
  color_name?: string | null;
  label_weight?: number | null;
  weight_used?: number | null;
  low_stock_threshold_pct?: number | null;
  archived_at?: string | null;
}

/** Percent of filament left on a spool, null when the label weight is unknown. */
export function spoolRemainingPct(spool: SpoolLike): number | null {
  if (!spool.label_weight || spool.label_weight <= 0) return null;
  return clampPercent(
    ((spool.label_weight - (spool.weight_used ?? 0)) / spool.label_weight) * 100,
  );
}

export function isLowSpool(spool: SpoolLike, defaultThreshold: number): boolean {
  if (spool.archived_at) return false;
  const remaining = spoolRemainingPct(spool);
  if (remaining === null) return false;
  return remaining <= (spool.low_stock_threshold_pct ?? defaultThreshold);
}

export function lowSpoolCount(spools: SpoolLike[], defaultThreshold: number): number {
  return spools.filter((spool) => isLowSpool(spool, defaultThreshold)).length;
}

export interface MaintenanceItemLike {
  enabled?: boolean;
  is_due?: boolean;
  is_warning?: boolean;
  maintenance_type_name?: string | null;
}

export interface MaintenanceOverviewLike {
  printer_id: number;
  printer_name: string;
  maintenance_items: MaintenanceItemLike[];
}

export function maintenanceAttentionCount(overview: MaintenanceOverviewLike[]): number {
  return overview.reduce(
    (count, printer) =>
      count +
      printer.maintenance_items.filter((item) => item.enabled && (item.is_due || item.is_warning))
        .length,
    0,
  );
}

export type AlertTone = 'red' | 'amber';

export interface CommandCenterAlert {
  id: string;
  title: string;
  /** i18n key plus its interpolation values; the page renders it. */
  detailKey: string;
  detailValues?: Record<string, string | number>;
  tone: AlertTone;
}

/**
 * Everything that wants a hand, in one list: printers in trouble, spools
 * running low, maintenance due or warning. Ordered printers first, because a
 * stopped printer costs more than a low spool.
 */
export function buildCommandCenterAlerts(
  entries: FleetEntry[],
  spools: SpoolLike[],
  overview: MaintenanceOverviewLike[],
  defaultThreshold: number,
): CommandCenterAlert[] {
  const printerAlerts: CommandCenterAlert[] = [];
  entries.forEach((entry) => {
    const state = classifyFleetState(entry.status, entry.hasKnownHmsErrors);
    if (state === 'alert') {
      printerAlerts.push({
        id: `printer-alert-${entry.printer.id}`,
        title: entry.printer.name,
        detailKey: 'farm.alerts.printerError',
        tone: 'red',
      });
    } else if (state === 'paused') {
      printerAlerts.push({
        id: `printer-paused-${entry.printer.id}`,
        title: entry.printer.name,
        detailKey: 'farm.alerts.printerPaused',
        tone: 'amber',
      });
    } else if (state === 'offline') {
      printerAlerts.push({
        id: `printer-offline-${entry.printer.id}`,
        title: entry.printer.name,
        detailKey: 'farm.alerts.printerOffline',
        tone: 'red',
      });
    }
  });

  const stockAlerts: CommandCenterAlert[] = spools
    .filter((spool) => isLowSpool(spool, defaultThreshold))
    .map((spool) => ({
      id: `spool-${spool.id}`,
      title: spool.color_name ? `${spool.color_name} ${spool.material}` : spool.material,
      detailKey: 'farm.alerts.spoolLow',
      detailValues: { percent: spoolRemainingPct(spool) ?? 0 },
      tone: 'amber' as const,
    }));

  const maintenanceAlerts: CommandCenterAlert[] = overview.flatMap((printer) =>
    printer.maintenance_items
      .filter((item) => item.enabled && (item.is_due || item.is_warning))
      .map((item, index) => ({
        id: `maintenance-${printer.printer_id}-${index}`,
        title: printer.printer_name,
        detailKey: 'farm.alerts.maintenance',
        detailValues: { item: item.maintenance_type_name || '' },
        tone: item.is_due ? ('red' as const) : ('amber' as const),
      })),
  );

  return [...printerAlerts, ...stockAlerts, ...maintenanceAlerts];
}
