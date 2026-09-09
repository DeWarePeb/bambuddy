/**
 * Fleet-size audience split for the sponsor surfaces.
 *
 * Upstream splits the pitch on fleet size so a hobbyist is never asked to buy a
 * support contract and a print farm is never asked to chip in $5. This fork
 * removes the commercial half instead of retargeting it, and these tests pin
 * that — the failure they guard against is a rebase quietly restoring upstream's
 * version and putting a farm running Printhok back in front of an offer of "a
 * support contract, an invoice and a named contact" that nobody here can honour.
 */
import { describe, it, expect } from 'vitest';
import {
  BUSINESS_FLEET_THRESHOLD,
  fleetAudience,
  sponsorHref,
} from '../../utils/fleetAudience';

describe('fleetAudience', () => {
  it('treats every fleet size as personal on this fork', () => {
    expect(fleetAudience(0)).toBe('personal');
    expect(fleetAudience(1)).toBe('personal');
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD - 1)).toBe('personal');
  });

  it('does not switch to the commercial pitch on a business-sized fleet', () => {
    // The one that matters. Upstream returns 'business' from here up.
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD)).toBe('personal');
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD + 1)).toBe('personal');
    expect(fleetAudience(40)).toBe('personal');
  });
});

describe('sponsorHref', () => {
  it('sends everyone to maziggy, whose application this is', () => {
    expect(sponsorHref('personal', 'app-settings')).toBe('https://github.com/sponsors/maziggy');
    expect(sponsorHref('business', 'app-settings')).toBe('https://github.com/sponsors/maziggy');
  });

  it('does not point at upstream commercial pages', () => {
    for (const from of ['app-settings', 'app-toast-prints-10']) {
      for (const audience of ['personal', 'business'] as const) {
        expect(sponsorHref(audience, from)).not.toContain('business.html');
        expect(sponsorHref(audience, from)).not.toContain('bambuddy.cool');
      }
    }
  });

  it('drops the Matomo attribution param', () => {
    // Upstream tags these links so its funnel is measurable. Sending it from a
    // fork install files Printhok's users under Bambuddy's analytics, which
    // measures the wrong thing for both projects.
    expect(sponsorHref('personal', 'app-toast-prints-10')).not.toContain('?from=');
    expect(sponsorHref('business', 'app-settings')).not.toContain('?from=');
  });
});
