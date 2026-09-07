/**
 * Display brand (voron patch series).
 *
 * The backend, service, paths and API stay "Bambuddy"; only what the user
 * sees is renamed. Everything comes from one static file, `/brand.json`,
 * so the name and logo can be changed on the server (static/brand.json)
 * without a rebuild, and upstream's hardcoded strings elsewhere are left
 * alone to keep the rebase surface small.
 */

export interface Brand {
  name: string;
  logoDark: string;
  logoLight: string;
}

export const DEFAULT_BRAND: Brand = {
  name: 'Bambuddy',
  logoDark: '/img/bambuddy_logo_dark_transparent.png',
  logoLight: '/img/bambuddy_logo_light.png',
};

let current: Brand = DEFAULT_BRAND;

export function getBrand(): Brand {
  return current;
}

/** Fetch /brand.json once; falls back to the defaults on any error. */
export async function loadBrand(): Promise<Brand> {
  try {
    const res = await fetch('/brand.json', { cache: 'no-store' });
    if (res.ok) {
      const data = (await res.json()) as Partial<Brand>;
      current = {
        name: typeof data.name === 'string' && data.name.trim() ? data.name.trim() : DEFAULT_BRAND.name,
        logoDark: typeof data.logoDark === 'string' && data.logoDark ? data.logoDark : DEFAULT_BRAND.logoDark,
        logoLight: typeof data.logoLight === 'string' && data.logoLight ? data.logoLight : DEFAULT_BRAND.logoLight,
      };
    }
  } catch {
    // no brand.json (or blocked): keep upstream branding
  }
  document.title = current.name;
  return current;
}
