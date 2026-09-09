/**
 * What a finished print should sell for (fork).
 *
 * Upstream's finance module is a chargeback system — cost centres, budgets,
 * per-user wallets. It answers "who owes what for the machine time". A shop
 * asks the other question, and nothing upstream answers it, even though the
 * inputs are all on the archive row already: the filament `cost`, the
 * `energy_cost` the smart plug measured, and `print_time_seconds`.
 *
 * Kept as a pure function so the number can be tested without rendering, and
 * so it reacts to a settings change without a round trip.
 */

export interface PricingSettings {
  pricing_enabled: boolean;
  pricing_labour_per_hour: number;
  pricing_markup: number;
  pricing_floor: number;
}

/** Only the archive fields the price depends on. */
export interface PricedArchive {
  cost?: number | null;
  energy_cost?: number | null;
  print_time_seconds?: number | null;
}

export interface PrintPrice {
  /** Filament plus energy: what the archive actually measured. */
  materialCost: number;
  /** Labour rate times print hours. Zero when no rate is set. */
  labourCost: number;
  /** Everything the print cost to make, before markup. */
  unitCost: number;
  /** What to ask for it. */
  suggestedPrice: number;
  /**
   * Share of the selling price that is not cost, 0–1. Null when the price is
   * zero (nothing to take a share of), and negative when a markup below 1
   * prices the print under its own cost — which is worth showing, not hiding.
   */
  margin: number | null;
  /** True when the floor, not the markup, decided the price. */
  onFloor: boolean;
}

const SECONDS_PER_HOUR = 3600;

/**
 * Returns null when pricing is off, or when the print carries no cost
 * information at all — a suggestion built on nothing is worse than no
 * suggestion. A print with filament cost but no energy reading is priced with
 * energy treated as zero, because a plug without an energy meter is a normal
 * setup rather than missing data.
 */
export function priceForPrint(
  archive: PricedArchive,
  settings: Partial<PricingSettings> | null | undefined,
): PrintPrice | null {
  if (!settings?.pricing_enabled) return null;
  if (archive.cost == null && archive.energy_cost == null) return null;

  const materialCost = (archive.cost ?? 0) + (archive.energy_cost ?? 0);

  // Negative settings are treated as unset rather than refused: a stray minus
  // in a number field should not make the whole card show a nonsense price.
  const labourPerHour = Math.max(0, settings.pricing_labour_per_hour ?? 0);
  const hours = Math.max(0, archive.print_time_seconds ?? 0) / SECONDS_PER_HOUR;
  const labourCost = labourPerHour * hours;

  const unitCost = materialCost + labourCost;
  const markup = Math.max(0, settings.pricing_markup ?? 0);
  const floor = Math.max(0, settings.pricing_floor ?? 0);

  const marked = unitCost * markup;
  const suggestedPrice = Math.max(marked, floor);

  return {
    materialCost,
    labourCost,
    unitCost,
    suggestedPrice,
    margin: suggestedPrice > 0 ? (suggestedPrice - unitCost) / suggestedPrice : null,
    onFloor: suggestedPrice > marked,
  };
}
