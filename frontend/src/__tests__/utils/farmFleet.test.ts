/** Voron patch series (B11): farm command center arithmetic. */

import { describe, expect, it } from 'vitest';
import {
  buildCommandCenterAlerts,
  buildPrinterGroupMap,
  classifyFleetState,
  completedToday,
  countStates,
  filterFleetGroups,
  fleetUtilization,
  groupFleet,
  isLowSpool,
  lowSpoolCount,
  maintenanceAttentionCount,
  projectProgress,
  spoolRemainingPct,
  UNGROUPED,
  type FleetEntry,
} from '../../utils/farmFleet';

const entry = (
  id: number,
  name: string,
  status: Record<string, unknown> | undefined,
  hasKnownHmsErrors = false,
  location?: string,
): FleetEntry => ({ printer: { id, name, location }, status, hasKnownHmsErrors });

describe('classifyFleetState', () => {
  it('treats a disconnected printer as offline whatever the state says', () => {
    // A stale RUNNING from a box that has gone away must not read as printing.
    expect(classifyFleetState({ connected: false, state: 'RUNNING' })).toBe('offline');
    expect(classifyFleetState(undefined)).toBe('offline');
  });

  it('maps the canonical states', () => {
    expect(classifyFleetState({ connected: true, state: 'RUNNING' })).toBe('printing');
    expect(classifyFleetState({ connected: true, state: 'PAUSE' })).toBe('paused');
    expect(classifyFleetState({ connected: true, state: 'IDLE' })).toBe('idle');
    expect(classifyFleetState({ connected: true, state: null })).toBe('idle');
  });

  it('treats FINISH and FAILED as terminal, not as an alert', () => {
    // FAILED covers user cancellation; only an attached HMS code is an alert.
    expect(classifyFleetState({ connected: true, state: 'FINISH' })).toBe('idle');
    expect(classifyFleetState({ connected: true, state: 'FAILED' })).toBe('idle');
  });

  it('escalates to alert only on a known HMS error', () => {
    expect(classifyFleetState({ connected: true, state: 'RUNNING' }, true)).toBe('alert');
    expect(classifyFleetState({ connected: true, state: 'FAILED' }, true)).toBe('alert');
    // Offline still wins: nothing can be reported about an unreachable printer.
    expect(classifyFleetState({ connected: false, state: 'FAILED' }, true)).toBe('offline');
  });
});

describe('countStates and fleetUtilization', () => {
  const fleet = [
    entry(1, 'A', { connected: true, state: 'RUNNING' }),
    entry(2, 'B', { connected: true, state: 'RUNNING' }),
    entry(3, 'C', { connected: true, state: 'IDLE' }),
    entry(4, 'D', { connected: false, state: 'IDLE' }),
  ];

  it('counts every printer exactly once', () => {
    const counts = countStates(fleet);
    expect(counts).toEqual({ printing: 2, paused: 0, idle: 1, alert: 0, offline: 1 });
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(fleet.length);
  });

  it('reports utilization as printing over total, and 0 for an empty fleet', () => {
    expect(fleetUtilization(fleet)).toBe(50);
    expect(fleetUtilization([])).toBe(0);
  });
});

describe('groupFleet', () => {
  const fleet = [
    entry(1, 'Voron', { connected: true }, false, 'Workshop'),
    entry(2, 'P2S', { connected: true }, false, 'Workshop'),
    entry(3, 'X2D', { connected: true }, false, undefined),
  ];

  it('prefers the fleet group over the location', () => {
    const buckets = groupFleet(fleet, [{ name: 'Shop', printer_ids: [1, 2] }]);
    const names = buckets.map((bucket) => bucket.name).sort();
    expect(names).toEqual(['Shop', UNGROUPED]);
    expect(buckets.find((b) => b.name === 'Shop')!.items).toHaveLength(2);
  });

  it('falls back to location, then to Ungrouped', () => {
    const buckets = groupFleet(fleet, []);
    expect(buckets.map((b) => b.name).sort()).toEqual([UNGROUPED, 'Workshop']);
  });

  it('places every printer exactly once', () => {
    const buckets = groupFleet(fleet, [{ name: 'Shop', printer_ids: [1] }]);
    const ids = buckets.flatMap((b) => b.items.map((i) => i.printer.id)).sort();
    expect(ids).toEqual([1, 2, 3]);
  });

  it('maps printer ids to group names', () => {
    const map = buildPrinterGroupMap([
      { name: 'Shop', printer_ids: [1, 2] },
      { name: 'Lab', printer_ids: [3] },
    ]);
    expect(map.get(1)).toBe('Shop');
    expect(map.get(3)).toBe('Lab');
    expect(map.get(9)).toBeUndefined();
  });
});

describe('filterFleetGroups', () => {
  const buckets = groupFleet(
    [
      entry(1, 'Voron 2.4', { connected: true }),
      entry(2, 'P2S', { connected: true }),
    ],
    [
      { name: 'Klipper', printer_ids: [1] },
      { name: 'Bambu', printer_ids: [2] },
    ],
  );

  it('drops buckets left empty by the search', () => {
    const filtered = filterFleetGroups(buckets, 'voron', 'all');
    expect(filtered).toHaveLength(1);
    expect(filtered[0].name).toBe('Klipper');
  });

  it('is case-insensitive and ignores surrounding space', () => {
    expect(filterFleetGroups(buckets, '  P2S  ', 'all')[0].items[0].printer.name).toBe('P2S');
  });

  it('applies the group filter', () => {
    expect(filterFleetGroups(buckets, '', 'Bambu').map((b) => b.name)).toEqual(['Bambu']);
    expect(filterFleetGroups(buckets, '', 'all')).toHaveLength(2);
  });
});

describe('projectProgress', () => {
  it('prefers the server percentage', () => {
    expect(
      projectProgress({ progress_percent: 42, completed_count: 0, archive_count: 0 }),
    ).toBe(42);
  });

  it('falls back to parts, then plates', () => {
    expect(
      projectProgress({ target_parts_count: 4, completed_count: 1, archive_count: 0 }),
    ).toBe(25);
    expect(
      projectProgress({ target_count: 4, completed_count: 0, archive_count: 3 }),
    ).toBe(75);
  });

  it('returns null when there is nothing to measure against', () => {
    // The page shows a dash rather than a misleading 0 %.
    expect(projectProgress({ completed_count: 5, archive_count: 5 })).toBeNull();
    expect(projectProgress({ target_parts_count: 0, completed_count: 0, archive_count: 0 })).toBeNull();
  });

  it('clamps out-of-range values', () => {
    expect(projectProgress({ progress_percent: 140, completed_count: 0, archive_count: 0 })).toBe(100);
    expect(projectProgress({ progress_percent: -5, completed_count: 0, archive_count: 0 })).toBe(0);
  });
});

describe('completedToday', () => {
  const now = new Date('2026-09-08T18:00:00');

  it('counts only completed items finished on the same calendar day', () => {
    const queue = [
      { status: 'completed', completed_at: '2026-09-08T09:00:00' },
      { status: 'completed', completed_at: '2026-09-07T23:59:00' },
      { status: 'printing', completed_at: '2026-09-08T10:00:00' },
      { status: 'completed', completed_at: null },
    ];
    expect(completedToday(queue, now)).toBe(1);
  });
});

describe('spools', () => {
  it('computes remaining percent, or null without a label weight', () => {
    expect(spoolRemainingPct({ id: 1, material: 'PLA', label_weight: 1000, weight_used: 250 })).toBe(75);
    expect(spoolRemainingPct({ id: 2, material: 'PLA', label_weight: 0 })).toBeNull();
    expect(spoolRemainingPct({ id: 3, material: 'PLA' })).toBeNull();
  });

  it('uses the per-spool threshold over the global one', () => {
    const spool = { id: 1, material: 'PLA', label_weight: 1000, weight_used: 850 };
    expect(isLowSpool(spool, 20)).toBe(true);
    expect(isLowSpool({ ...spool, low_stock_threshold_pct: 10 }, 20)).toBe(false);
  });

  it('never counts an archived spool, however empty', () => {
    const spool = {
      id: 1,
      material: 'PLA',
      label_weight: 1000,
      weight_used: 999,
      archived_at: '2026-01-01T00:00:00',
    };
    expect(isLowSpool(spool, 20)).toBe(false);
    expect(lowSpoolCount([spool], 20)).toBe(0);
  });
});

describe('maintenanceAttentionCount', () => {
  it('counts due and warning items, and ignores disabled ones', () => {
    expect(
      maintenanceAttentionCount([
        {
          printer_id: 1,
          printer_name: 'Voron',
          maintenance_items: [
            { enabled: true, is_due: true },
            { enabled: true, is_warning: true },
            { enabled: true },
            { enabled: false, is_due: true },
          ],
        },
      ]),
    ).toBe(2);
  });
});

describe('buildCommandCenterAlerts', () => {
  it('lists printers first, then stock, then maintenance', () => {
    const alerts = buildCommandCenterAlerts(
      [entry(1, 'Voron', { connected: false })],
      [{ id: 7, material: 'PETG', color_name: 'Orange', label_weight: 1000, weight_used: 900 }],
      [
        {
          printer_id: 1,
          printer_name: 'Voron',
          maintenance_items: [{ enabled: true, is_due: true, maintenance_type_name: 'Lubricate' }],
        },
      ],
      20,
    );
    expect(alerts.map((a) => a.detailKey)).toEqual([
      'farm.alerts.printerOffline',
      'farm.alerts.spoolLow',
      'farm.alerts.maintenance',
    ]);
    expect(alerts[1].title).toBe('Orange PETG');
    expect(alerts[1].detailValues).toEqual({ percent: 10 });
    // Due is red, a warning would be amber.
    expect(alerts[2].tone).toBe('red');
  });

  it('is empty when everything is healthy', () => {
    const alerts = buildCommandCenterAlerts(
      [entry(1, 'Voron', { connected: true, state: 'RUNNING' })],
      [{ id: 7, material: 'PLA', label_weight: 1000, weight_used: 100 }],
      [],
      20,
    );
    expect(alerts).toEqual([]);
  });
});
