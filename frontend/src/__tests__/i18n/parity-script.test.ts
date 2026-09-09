import { describe, it, expect } from 'vitest';
// @ts-expect-error -- .mjs script with no type declarations; pure JS import is fine for tests
import { compareLocales } from '../../../scripts/check-i18n-parity.mjs';

type LocaleMap = Map<string, string>;

const toMap = (obj: Record<string, string>): LocaleMap => new Map(Object.entries(obj));

const hasReport = (
  reports: Array<{ label: string; items: string[] }>,
  labelSubstr: string,
  itemSubstr?: string,
): boolean =>
  reports.some(
    (r) =>
      r.label.includes(labelSubstr) &&
      (itemSubstr === undefined || r.items.some((i) => i.includes(itemSubstr))),
  );

describe('compareLocales (parity-script self-test)', () => {
  it('passes when all locales match en', () => {
    const en = toMap({ 'a.b': 'hello {{name}}', 'count_one': 'one', 'count_other': 'many' });
    const result = compareLocales({
      en,
      'zh-CN': toMap({ 'a.b': '你好 {{name}}', 'count_one': '一', 'count_other': '多' }),
      'zh-TW': toMap({ 'a.b': '你好 {{name}}', 'count_one': '一', 'count_other': '多' }),
    });
    expect(result.failed).toBe(false);
    expect(result.reports).toEqual([]);
  });

  it('flags keys missing from a non-en locale', () => {
    const result = compareLocales({
      en: toMap({ 'a.b': 'x', 'a.c': 'y' }),
      'zh-TW': toMap({ 'a.b': 'x' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'zh-TW: missing keys vs en', 'a.c')).toBe(true);
  });

  it('flags keys that exist in a non-en locale but not in en', () => {
    const result = compareLocales({
      en: toMap({ 'a.b': 'x' }),
      'zh-CN': toMap({ 'a.b': 'x', 'a.stray': 'extra' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'zh-CN: extra keys vs en', 'a.stray')).toBe(true);
  });

  it('accepts the plural categories a locale has and en does not', () => {
    // Russian counts in one/few/many/other, so "3 катушки" and "5 катушек"
    // need suffixes en never writes. Those are the same key, spelled for the
    // grammar — not drift.
    const result = compareLocales({
      en: toMap({ spool_one: 'spool', spool_other: 'spools' }),
      ru: toMap({
        spool_one: 'катушка',
        spool_few: 'катушки',
        spool_many: 'катушек',
        spool_other: 'катушки',
      }),
    });
    expect(result.failed).toBe(false);
  });

  it('still flags an extra key whose suffix is not a plural category', () => {
    const result = compareLocales({
      en: toMap({ spool_one: 'spool', spool_other: 'spools' }),
      ru: toMap({ spool_one: 'катушка', spool_other: 'катушки', spool_maybe: 'опечатка' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'ru: extra keys vs en', 'spool_maybe')).toBe(true);
  });

  it('still flags a plural-category suffix on a key en does not pluralize', () => {
    // No `spool_other` in en means this locale invented the plural, so the
    // count never reaches i18next and the extra forms are dead strings.
    const result = compareLocales({
      en: toMap({ spool: 'spool' }),
      ru: toMap({ spool: 'катушка', spool_many: 'катушек' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'ru: extra keys vs en', 'spool_many')).toBe(true);
  });

  it('does not accept a plural category the locale itself lacks', () => {
    // German has no "few"; only Slavic-style locales do.
    const result = compareLocales({
      en: toMap({ spool_one: 'spool', spool_other: 'spools' }),
      de: toMap({ spool_one: 'Spule', spool_other: 'Spulen', spool_few: 'Spulen' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'de: extra keys vs en', 'spool_few')).toBe(true);
  });

  it('flags placeholder mismatch (missing placeholder in translation)', () => {
    const result = compareLocales({
      en: toMap({ greeting: 'Hello {{name}}!' }),
      'zh-CN': toMap({ greeting: '你好!' }), // {{name}} dropped
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'placeholder mismatch', 'greeting')).toBe(true);
  });

  it('flags placeholder mismatch (translation introduces unknown placeholder)', () => {
    // This is the exact class of bug the zh-CN sync caught:
    // fileManager.uploadFailed had a stray {{count}} copied from a sibling key.
    const result = compareLocales({
      en: toMap({ uploadFailed: 'Upload failed' }),
      'zh-CN': toMap({ uploadFailed: '{{count}} 个失败' }),
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'placeholder mismatch', 'uploadFailed')).toBe(true);
  });

  it('flags missing plural suffix keys in a non-en locale', () => {
    const result = compareLocales({
      en: toMap({ item_one: 'item', item_other: 'items' }),
      'zh-TW': toMap({ item_one: '項目' }), // item_other missing
    });
    expect(result.failed).toBe(true);
    expect(hasReport(result.reports, 'plural key mismatch', 'missing _other')).toBe(true);
  });

  it('flags a non-en _one key that does not exist in en', () => {
    const result = compareLocales({
      en: toMap({ item: 'item' }),
      'zh-CN': toMap({ item: '项', item_one: '一项' }), // en never plural-gated this
    });
    expect(result.failed).toBe(true);
    expect(
      hasReport(result.reports, 'plural key mismatch', 'unexpected _one not present in en'),
    ).toBe(true);
  });

  it('throws when the en locale is absent', () => {
    expect(() =>
      compareLocales({ 'zh-CN': toMap({ a: 'x' }) } as Record<string, LocaleMap>),
    ).toThrow(/locales\.en/);
  });
});
