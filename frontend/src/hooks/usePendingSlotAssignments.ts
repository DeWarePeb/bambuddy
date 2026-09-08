/**
 * Pending spool-to-slot assignments (voron B8).
 *
 * The hook and its query key live here rather than beside the components in
 * `NextSlotAssignment.tsx`: exporting a non-component from a component module
 * breaks React Fast Refresh for that file, which is what
 * `react-refresh/only-export-components` is about. Nothing else in the app
 * mixes the two, so this one follows suit.
 */

import { useQuery } from '@tanstack/react-query';

import { api } from '../api/client';

export const PENDING_SLOT_QUERY_KEY = ['pending-slot-assignments'] as const;

export function usePendingSlotAssignments(enabled = true) {
  return useQuery({
    queryKey: PENDING_SLOT_QUERY_KEY,
    queryFn: () => api.getPendingSlotAssignments(),
    enabled,
    refetchInterval: 30000,
  });
}
