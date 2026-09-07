import { useMemo, useState } from 'react';
import { Database, Loader2, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../api/client';
import type {
  OpenFilamentDatabaseBrandSummary,
  OpenFilamentDatabaseFilamentSummary,
  OpenFilamentDatabaseMaterialSummary,
  OpenFilamentDatabaseVariantSummary,
} from '../../api/client';
import type { SectionProps } from './types';
import { OFDB_DATA_ORIGIN } from './types';

// Open Filament Database lookup for the Add Spool form (voron B6). OFDB is a
// static brand -> material -> filament -> colour tree with no global search,
// so the panel walks that tree: pick a brand, a material, a filament family,
// then a colour, and the variant's spool_prefill block is copied into the form.
// Nothing here is required; every field stays editable afterwards.

interface OpenFilamentDatabaseSearchProps extends SectionProps {
  setPresetInputValue: (value: string) => void;
}

export function OpenFilamentDatabaseSearch({ updateField, setPresetInputValue }: OpenFilamentDatabaseSearchProps) {
  const { t } = useTranslation();
  const [brandQuery, setBrandQuery] = useState('');
  const [brands, setBrands] = useState<OpenFilamentDatabaseBrandSummary[]>([]);
  const [brandsLoaded, setBrandsLoaded] = useState(false);
  const [selectedBrand, setSelectedBrand] = useState<OpenFilamentDatabaseBrandSummary | null>(null);
  const [materials, setMaterials] = useState<OpenFilamentDatabaseMaterialSummary[]>([]);
  const [selectedMaterial, setSelectedMaterial] = useState<OpenFilamentDatabaseMaterialSummary | null>(null);
  const [filamentQuery, setFilamentQuery] = useState('');
  const [filaments, setFilaments] = useState<OpenFilamentDatabaseFilamentSummary[]>([]);
  const [selectedFilament, setSelectedFilament] = useState<OpenFilamentDatabaseFilamentSummary | null>(null);
  const [variants, setVariants] = useState<OpenFilamentDatabaseVariantSummary[]>([]);
  const [appliedVariant, setAppliedVariant] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errorMessage = (err: unknown, fallbackKey: string) =>
    err instanceof ApiError ? err.message : t(fallbackKey);

  const filteredBrands = useMemo(() => {
    const search = brandQuery.trim().toLowerCase();
    const matching = search
      ? brands.filter(b => b.name.toLowerCase().includes(search) || b.slug.toLowerCase().includes(search))
      : brands;
    return [...matching]
      .sort((a, b) => {
        const aExact = a.name.toLowerCase() === search;
        const bExact = b.name.toLowerCase() === search;
        if (aExact !== bExact) return aExact ? -1 : 1;
        return a.name.localeCompare(b.name);
      })
      .slice(0, 30);
  }, [brandQuery, brands]);

  const filteredFilaments = useMemo(() => {
    const search = filamentQuery.trim().toLowerCase();
    if (!search) return filaments;
    return filaments.filter(f => f.name.toLowerCase().includes(search) || f.slug.toLowerCase().includes(search));
  }, [filamentQuery, filaments]);

  const resetFrom = (level: 'brand' | 'material' | 'filament') => {
    if (level === 'brand') {
      setSelectedBrand(null);
      setMaterials([]);
    }
    if (level !== 'filament') {
      setSelectedMaterial(null);
      setFilaments([]);
      setFilamentQuery('');
    }
    setSelectedFilament(null);
    setVariants([]);
    setAppliedVariant(null);
    setError(null);
  };

  const loadBrands = async () => {
    if (brandsLoaded || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.getOpenFilamentDatabaseBrands();
      setBrands(response.brands);
      setBrandsLoaded(true);
      if (response.brands.length === 0) setError(t('inventory.openFilamentDatabase.noBrands'));
    } catch (err) {
      setError(errorMessage(err, 'inventory.openFilamentDatabase.brandLoadFailed'));
    } finally {
      setLoading(false);
    }
  };

  const selectBrand = async (brand: OpenFilamentDatabaseBrandSummary) => {
    resetFrom('brand');
    setSelectedBrand(brand);
    setBrandQuery(brand.name);
    setLoading(true);
    try {
      const response = await api.getOpenFilamentDatabaseBrand(brand.slug);
      setMaterials(response.materials);
      if (response.materials.length === 0) setError(t('inventory.openFilamentDatabase.noMaterials'));
    } catch (err) {
      setError(errorMessage(err, 'inventory.openFilamentDatabase.materialLoadFailed'));
    } finally {
      setLoading(false);
    }
  };

  const selectMaterial = async (material: OpenFilamentDatabaseMaterialSummary) => {
    if (!selectedBrand) return;
    resetFrom('material');
    setSelectedMaterial(material);
    setLoading(true);
    try {
      // The list for one brand + material is short; filter it client-side.
      const response = await api.searchOpenFilamentDatabase(selectedBrand.slug, material.material, '');
      setFilaments(response.filaments);
      if (response.filaments.length === 0) setError(t('inventory.openFilamentDatabase.noFilaments'));
    } catch (err) {
      setError(errorMessage(err, 'inventory.openFilamentDatabase.searchFailed'));
    } finally {
      setLoading(false);
    }
  };

  const selectFilament = async (filament: OpenFilamentDatabaseFilamentSummary) => {
    if (!selectedBrand || !selectedMaterial) return;
    resetFrom('filament');
    setSelectedFilament(filament);
    setLoading(true);
    try {
      const response = await api.getOpenFilamentDatabaseFilament(
        selectedBrand.slug,
        selectedMaterial.material,
        filament.slug,
      );
      setVariants(response.variants);
      if (response.variants.length === 0) setError(t('inventory.openFilamentDatabase.noVariants'));
    } catch (err) {
      setError(errorMessage(err, 'inventory.openFilamentDatabase.filamentLoadFailed'));
    } finally {
      setLoading(false);
    }
  };

  const selectVariant = async (variant: OpenFilamentDatabaseVariantSummary) => {
    if (!selectedBrand || !selectedMaterial || !selectedFilament) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.getOpenFilamentDatabaseVariant(
        selectedBrand.slug,
        selectedMaterial.material,
        selectedFilament.slug,
        variant.slug,
      );
      const prefill = response.spool_prefill;
      // Provenance first, so a Spoolman catalog link is dropped before the
      // linked fields change (SpoolFormModal.updateField).
      updateField('data_origin', OFDB_DATA_ORIGIN);
      if (prefill.brand) updateField('brand', prefill.brand);
      if (prefill.material) updateField('material', prefill.material);
      if (prefill.subtype) updateField('subtype', prefill.subtype);
      if (prefill.color_name) updateField('color_name', prefill.color_name);
      if (prefill.rgba) updateField('rgba', prefill.rgba);
      if (typeof prefill.label_weight === 'number') updateField('label_weight', prefill.label_weight);
      if (typeof prefill.core_weight === 'number') {
        updateField('core_weight', prefill.core_weight);
        updateField('core_weight_catalog_id', null);
      }
      if (prefill.slicer_filament) updateField('slicer_filament', prefill.slicer_filament);
      if (prefill.slicer_filament_name) setPresetInputValue(prefill.slicer_filament_name);
      if (typeof prefill.nozzle_temp_min === 'number') updateField('nozzle_temp_min', prefill.nozzle_temp_min);
      if (typeof prefill.nozzle_temp_max === 'number') updateField('nozzle_temp_max', prefill.nozzle_temp_max);
      setAppliedVariant(variant.id);
    } catch (err) {
      setError(errorMessage(err, 'inventory.openFilamentDatabase.variantLoadFailed'));
    } finally {
      setLoading(false);
    }
  };

  const inputClass =
    'w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green';
  const chipClass = (active: boolean) =>
    `px-3 py-2 rounded-lg border border-bambu-dark-tertiary text-left text-sm hover:bg-bambu-dark-tertiary ${
      active ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
    }`;

  return (
    <div className="p-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary/60 space-y-3">
      <div className="flex items-start gap-2 text-sm text-white">
        <Database className="w-4 h-4 mt-0.5 text-bambu-green flex-shrink-0" />
        <div>
          <span className="block font-medium">{t('inventory.openFilamentDatabase.title')}</span>
          <span className="block text-xs text-bambu-gray mt-0.5">{t('inventory.openFilamentDatabase.hint')}</span>
        </div>
        {loading && <Loader2 className="w-4 h-4 ml-auto animate-spin text-bambu-gray" />}
      </div>

      {/* Brand */}
      <div className="space-y-1">
        <label className="block text-xs font-medium text-bambu-gray">
          {t('inventory.openFilamentDatabase.brandLabel')}
        </label>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          <input
            type="text"
            className={inputClass}
            placeholder={t('inventory.openFilamentDatabase.brandPlaceholder')}
            value={brandQuery}
            onFocus={() => void loadBrands()}
            onChange={(e) => {
              setBrandQuery(e.target.value);
              resetFrom('brand');
            }}
          />
        </div>
        {!selectedBrand && filteredBrands.length > 0 && (
          <div className="max-h-40 overflow-y-auto rounded-lg border border-bambu-dark-tertiary">
            {filteredBrands.map(brand => (
              <button
                key={brand.id}
                type="button"
                className="w-full px-3 py-2 text-left text-sm text-white hover:bg-bambu-dark-tertiary"
                onClick={() => void selectBrand(brand)}
              >
                <span className="block">{brand.name}</span>
                <span className="block text-xs text-bambu-gray">
                  {t('inventory.openFilamentDatabase.materialCount', { count: brand.material_count })}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Material */}
      {selectedBrand && materials.length > 0 && (
        <div className="space-y-1">
          <label className="block text-xs font-medium text-bambu-gray">
            {t('inventory.openFilamentDatabase.materialLabel')}
          </label>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-1 max-h-36 overflow-y-auto">
            {materials.map(material => (
              <button
                key={material.id}
                type="button"
                className={chipClass(selectedMaterial?.id === material.id)}
                onClick={() => void selectMaterial(material)}
              >
                <span className="block font-medium">{material.material}</span>
                <span className="block text-xs text-bambu-gray">
                  {t('inventory.openFilamentDatabase.filamentCount', { count: material.filament_count })}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filament family */}
      {selectedMaterial && filaments.length > 0 && (
        <div className="space-y-1">
          <label className="block text-xs font-medium text-bambu-gray">
            {t('inventory.openFilamentDatabase.filamentLabel')}
          </label>
          {filaments.length > 6 && (
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
              <input
                type="text"
                className={inputClass}
                placeholder={t('inventory.openFilamentDatabase.filamentPlaceholder')}
                value={filamentQuery}
                onChange={(e) => setFilamentQuery(e.target.value)}
              />
            </div>
          )}
          <div className="max-h-40 overflow-y-auto rounded-lg border border-bambu-dark-tertiary">
            {filteredFilaments.map(filament => (
              <button
                key={filament.id}
                type="button"
                className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                  selectedFilament?.id === filament.id ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
                }`}
                onClick={() => void selectFilament(filament)}
              >
                <span className="block">{filament.name}</span>
                <span className="block text-xs text-bambu-gray">
                  {t('inventory.openFilamentDatabase.variantCount', { count: filament.variant_count })}
                </span>
              </button>
            ))}
            {filteredFilaments.length === 0 && (
              <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
            )}
          </div>
        </div>
      )}

      {/* Colour variant */}
      {selectedFilament && variants.length > 0 && (
        <div className="space-y-1">
          <label className="block text-xs font-medium text-bambu-gray">
            {t('inventory.openFilamentDatabase.variantLabel')}
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 max-h-48 overflow-y-auto">
            {variants.map(variant => (
              <button
                key={variant.id}
                type="button"
                className={`flex items-center gap-2 ${chipClass(appliedVariant === variant.id)}`}
                onClick={() => void selectVariant(variant)}
              >
                <span
                  className="w-4 h-4 rounded-full border border-white/20 flex-shrink-0"
                  style={{ backgroundColor: variant.color_hex || '#808080' }}
                />
                <span>
                  <span className="block">{variant.name}</span>
                  <span className="block text-xs text-bambu-gray">
                    {t('inventory.openFilamentDatabase.sizeCount', { count: variant.size_count })}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {appliedVariant && !error && (
        <p className="text-xs text-bambu-green">{t('inventory.openFilamentDatabase.applied')}</p>
      )}
      {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </div>
  );
}
