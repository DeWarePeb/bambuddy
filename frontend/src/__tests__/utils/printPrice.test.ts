import { describe, it, expect } from 'vitest';
import { priceForPrint, type PricingSettings } from '../../utils/printPrice';

const on: PricingSettings = {
  pricing_enabled: true,
  pricing_labour_per_hour: 0,
  pricing_markup: 2.5,
  pricing_floor: 0,
};

describe('priceForPrint', () => {
  it('is silent while pricing is switched off', () => {
    expect(priceForPrint({ cost: 2 }, { ...on, pricing_enabled: false })).toBeNull();
    expect(priceForPrint({ cost: 2 }, null)).toBeNull();
  });

  it('is silent when the print carries no cost at all', () => {
    // A suggestion built on nothing is worse than no suggestion.
    expect(priceForPrint({ cost: null, energy_cost: null }, on)).toBeNull();
  });

  it('adds filament and energy, then applies the markup', () => {
    const p = priceForPrint({ cost: 2, energy_cost: 0.4 }, on)!;
    expect(p.materialCost).toBeCloseTo(2.4);
    expect(p.unitCost).toBeCloseTo(2.4);
    expect(p.suggestedPrice).toBeCloseTo(6);
  });

  it('prices a print whose plug has no energy meter', () => {
    // Common setup; dropping these would silently exclude whole printers.
    const p = priceForPrint({ cost: 2, energy_cost: null }, on)!;
    expect(p.materialCost).toBeCloseTo(2);
    expect(p.suggestedPrice).toBeCloseTo(5);
  });

  it('charges labour by print hours', () => {
    const p = priceForPrint(
      { cost: 1, energy_cost: 0, print_time_seconds: 5400 },
      { ...on, pricing_labour_per_hour: 4, pricing_markup: 1 },
    )!;
    expect(p.labourCost).toBeCloseTo(6); // 1.5 h at 4/h
    expect(p.unitCost).toBeCloseTo(7);
    expect(p.suggestedPrice).toBeCloseTo(7);
  });

  it('lifts a cheap print to the floor and says so', () => {
    const p = priceForPrint({ cost: 0.4 }, { ...on, pricing_markup: 2, pricing_floor: 5 })!;
    expect(p.suggestedPrice).toBeCloseTo(5);
    expect(p.onFloor).toBe(true);
  });

  it('leaves a print above the floor alone', () => {
    const p = priceForPrint({ cost: 10 }, { ...on, pricing_markup: 2, pricing_floor: 5 })!;
    expect(p.suggestedPrice).toBeCloseTo(20);
    expect(p.onFloor).toBe(false);
  });

  it('reports a loss rather than hiding it', () => {
    // A markup below 1 prices under cost. Showing a negative margin is the
    // whole point of the number.
    const p = priceForPrint({ cost: 10 }, { ...on, pricing_markup: 0.5 })!;
    expect(p.suggestedPrice).toBeCloseTo(5);
    expect(p.margin).toBeLessThan(0);
  });

  it('has no margin to report when the price is zero', () => {
    const p = priceForPrint({ cost: 0, energy_cost: 0 }, { ...on, pricing_markup: 0 })!;
    expect(p.suggestedPrice).toBe(0);
    expect(p.margin).toBeNull();
  });

  it('treats negative settings as unset instead of producing nonsense', () => {
    const p = priceForPrint(
      { cost: 2, print_time_seconds: 3600 },
      { ...on, pricing_labour_per_hour: -5, pricing_markup: -1, pricing_floor: -3 },
    )!;
    expect(p.labourCost).toBe(0);
    expect(p.suggestedPrice).toBe(0);
  });

  it('ignores a negative print time', () => {
    const p = priceForPrint(
      { cost: 2, print_time_seconds: -100 },
      { ...on, pricing_labour_per_hour: 10, pricing_markup: 1 },
    )!;
    expect(p.labourCost).toBe(0);
    expect(p.unitCost).toBeCloseTo(2);
  });

  it('computes margin as the share of the price that is not cost', () => {
    const p = priceForPrint({ cost: 4 }, { ...on, pricing_markup: 4 })!;
    expect(p.suggestedPrice).toBeCloseTo(16);
    expect(p.margin).toBeCloseTo(0.75);
  });
});
