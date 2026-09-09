/**
 * Fleet-size audience split for the sponsor surfaces.
 *
 * An install with a large fleet is almost certainly a business (print farm,
 * workshop, makerspace), and a business has no use for a "chip in $5" ask — it
 * wants a support contract, an invoice and a named contact. The sponsor toast
 * and the Settings banner therefore swap their copy and CTA above this
 * threshold instead of showing the personal ask.
 *
 * Counts CONFIGURED printers, not active ones: `is_active` is the
 * maintenance-mode flag, so a farm with half its machines on the bench must not
 * flicker back to the hobbyist pitch.
 */
export const BUSINESS_FLEET_THRESHOLD = 5;

export type SponsorAudience = 'personal' | 'business';

export function fleetAudience(_printerCount: number): SponsorAudience {
  // Voron patch series: always 'personal' on this fork.
  //
  // Upstream's business tier offers a print farm "a support contract, an
  // invoice and a named contact". That offer is maziggy's to make about
  // Bambuddy, and it is not his to make about Printhok — a farm that hits a
  // Klipper bug here, follows this banner and buys a support contract has been
  // sold help for software the seller does not support. Same failure as the
  // Report a Bug button filing on his tracker, with money attached.
  //
  // The personal ask stays, and stays pointed at him: this fork is a patch
  // series on his application and the README says so. Only the commercial
  // funnel is removed.
  //
  // The threshold below is kept rather than deleted so upstream's version of
  // this file still rebases cleanly against ours.
  return 'personal';
}

/** Where the sponsor surfaces point. */
export function sponsorHref(_audience: SponsorAudience, _from: string): string {
  // maziggy's GitHub sponsors page rather than bambuddy.cool.
  //
  // The `?from=` tag was Matomo attribution for upstream's funnel; sending it
  // from fork installs would put Printhok's users in upstream's analytics as if
  // they were Bambuddy's, which measures the wrong thing for both of us. The
  // sponsors page needs no attribution to work.
  return 'https://github.com/sponsors/maziggy';
}
