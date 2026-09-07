import type { InventorySpool } from '../api/client';

/**
 * Return true when spool matches the search query across all searchable text fields.
 * Case-insensitive. Empty or whitespace-only query always returns true.
 * A query with several words matches when every word appears in at least one
 * field, so "abs black" finds a black ABS spool (material + colour) and
 * "bambu petg" a Bambu Lab PETG spool (brand + material).
 */
export function spoolMatchesQuery(spool: InventorySpool, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;

  const haystack = [
    String(spool.id),
    spool.material,
    spool.brand,
    spool.color_name,
    spool.subtype,
    spool.note,
    spool.slicer_filament_name,
    spool.storage_location,
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.toLowerCase());

  return terms.every((term) => haystack.some((value) => value.includes(term)));
}

/** Filter a spool list by a free-text search query. */
export function filterSpoolsByQuery(spools: InventorySpool[], query: string): InventorySpool[] {
  if (!query.trim()) return spools;
  return spools.filter((spool) => spoolMatchesQuery(spool, query));
}
