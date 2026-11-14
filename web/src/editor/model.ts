/** Pure changes to a campaign document. Each returns a new document; nothing is mutated. */

import type { Brand, CampaignDocument, Palette, Preset, Section, SectionDef } from '../api';

export const FONT_OPTIONS: Array<[stack: string, label: string]> = [
  ['Arial, Helvetica, sans-serif', 'Arial / Helvetica'],
  ['Georgia, serif', 'Georgia'],
  ['Verdana, Geneva, sans-serif', 'Verdana'],
  ['Tahoma, Geneva, sans-serif', 'Tahoma'],
  ['"Trebuchet MS", Arial, sans-serif', 'Trebuchet'],
  ['"Times New Roman", Times, serif', 'Times New Roman'],
];

export const PALETTE_LABELS: Record<keyof Palette, string> = {
  outer: 'Outside background',
  background: 'Email background',
  featured: 'Feature backgrounds',
  text: 'Body text',
  heading: 'Headings',
  link: 'Links',
  accent: 'Buttons',
  buttonText: 'Button text',
};

export const LAYOUT_LIMITS = {
  width: { label: 'Email width (px)', min: 400, max: 900 },
  padding: { label: 'Side padding (px)', min: 8, max: 70 },
  radius: { label: 'Corner radius (px)', min: 0, max: 32 },
  fontSize: { label: 'Text size (px)', min: 12, max: 24 },
} as const;

export type LayoutKey = keyof typeof LAYOUT_LIMITS;

/** The same rule the server uses to turn a section ID into a CSS class. */
export function cssId(id: string): string {
  return id.replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 64);
}

export function newSectionId(): string {
  return `s${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
}

export function createSection(type: string, definition: SectionDef): Section {
  return { id: newSectionId(), type, settings: { ...definition.defaults } };
}

const CLOSING_TYPES = new Set(['footer', 'unsubscribe']);

/** Add after `afterId`; with nothing selected, add at the end but above the footer. */
export function insertAfter(doc: CampaignDocument, section: Section, afterId: string | null): CampaignDocument {
  const sections = [...doc.sections];
  let index = afterId ? sections.findIndex((s) => s.id === afterId) + 1 : 0;
  if (index <= 0) {
    index = sections.length;
    while (index > 0 && CLOSING_TYPES.has(sections[index - 1]!.type)) index--;
  }
  sections.splice(index, 0, section);
  return { ...doc, sections };
}

export function updateSetting(doc: CampaignDocument, id: string, key: string, value: string): CampaignDocument {
  return {
    ...doc,
    sections: doc.sections.map((s) => (s.id === id ? { ...s, settings: { ...s.settings, [key]: value } } : s)),
  };
}

export function moveTo(doc: CampaignDocument, id: string, index: number): CampaignDocument {
  const sections = [...doc.sections];
  const from = sections.findIndex((s) => s.id === id);
  if (from < 0 || index < 0 || index >= sections.length || from === index) return doc;
  const [moved] = sections.splice(from, 1);
  sections.splice(index, 0, moved!);
  return { ...doc, sections };
}

export function duplicate(doc: CampaignDocument, id: string): [CampaignDocument, string] {
  const original = doc.sections.find((s) => s.id === id);
  if (!original) return [doc, id];
  const copy = { ...original, id: newSectionId(), settings: { ...original.settings } };
  return [insertAfter(doc, copy, id), copy.id];
}

export function remove(doc: CampaignDocument, id: string): CampaignDocument {
  return { ...doc, sections: doc.sections.filter((s) => s.id !== id) };
}

export function updateBrand(doc: CampaignDocument, change: Partial<Brand>): CampaignDocument {
  return { ...doc, brand: { ...doc.brand, ...change } };
}

export function updatePalette(
  doc: CampaignDocument, scheme: 'light' | 'dark', key: keyof Palette, value: string,
): CampaignDocument {
  return updateBrand(doc, { [scheme]: { ...doc.brand[scheme], [key]: value } });
}

/** Restyle: fonts, sizes and shapes from the preset; colours and logo stay. */
export function applyPreset(doc: CampaignDocument, preset: Preset): CampaignDocument {
  const brand: Brand = { ...doc.brand, ...preset.brand };
  if (preset.flatFeatured) {
    brand.light = { ...brand.light, featured: brand.light.background };
    brand.dark = { ...brand.dark, featured: brand.dark.background };
  }
  return { ...doc, brand };
}

/** A short line describing a section in the outline, from its first non-empty text. */
export function summary(section: Section, definition?: SectionDef): string {
  const values = definition ? definition.fields.map((f) => section.settings[f.key] || definition.defaults[f.key] || '') : Object.values(section.settings);
  const text = values.find((v) => v && !/^(https?:|data:|#|\d+$)/.test(v)) ?? '';
  return text.replace(/\s+/g, ' ').slice(0, 60);
}
