/** Right panel: settings for whatever is selected in the outline. */

import { useState } from 'preact/hooks';
import { api, type Brand, type CampaignDocument, type Palette, type SectionLibrary } from '../api';
import { Button, describe, Notice } from '../ui';
import { ImageField, SettingField } from './fields';
import { applyPreset, FONT_OPTIONS, LAYOUT_LIMITS, PALETTE_LABELS, updateBrand, updatePalette, type LayoutKey } from './model';

// ---------------------------------------------------------------------------
// A section

interface SectionProps {
  document: CampaignDocument;
  library: SectionLibrary;
  sectionId: string;
  readOnly: boolean;
  onSetting: (key: string, value: string) => void;
  onMove: (delta: -1 | 1) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

export function SectionInspector({ document, library, sectionId, readOnly, onSetting, onMove, onDuplicate, onDelete }: SectionProps) {
  const index = document.sections.findIndex((s) => s.id === sectionId);
  const section = document.sections[index];
  if (!section) return <p class="muted">Select a section to change it.</p>;
  const definition = library.sections[section.type];
  if (!definition) return <Notice tone="error">This section type ({section.type}) isn't available.</Notice>;

  return (
    <>
      <h2 class="inspector__title">{definition.label}</h2>
      <div class="form">
        {definition.fields.map((field) => (
          <SettingField
            key={`${section.id}-${field.key}`}
            field={field}
            value={section.settings[field.key] ?? definition.defaults[field.key] ?? ''}
            onValue={(value) => onSetting(field.key, value)}
            disabled={readOnly}
          />
        ))}
        {!definition.fields.length && <p class="muted">This section has no settings.</p>}
      </div>
      {['footer', 'unsubscribe'].includes(section.type) && (
        <p class="hint">Every email gets a working unsubscribe link and your postal address, even without this section.</p>
      )}
      {!readOnly && (
        <div class="inspector__actions">
          <Button onClick={() => onMove(-1)} disabled={index === 0}>↑ Up</Button>
          <Button onClick={() => onMove(1)} disabled={index === document.sections.length - 1}>↓ Down</Button>
          <Button onClick={onDuplicate}>Duplicate</Button>
          <Button variant="danger" onClick={onDelete}>Delete</Button>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Email details

export interface Details {
  name: string;
  subject: string;
  preheader: string;
}

export function EmailDetails({ details, readOnly, onChange }: {
  details: Details;
  readOnly: boolean;
  onChange: (change: Partial<Details>) => void;
}) {
  return (
    <>
      <h2 class="inspector__title">Email details</h2>
      <div class="form">
        <SettingField
          field={{ key: 'subject', label: 'Subject line', kind: 'text' }}
          value={details.subject}
          onValue={(subject) => onChange({ subject })}
          disabled={readOnly}
        />
        <label class="field" for="setting-preheader">
          <span>Preview text</span>
          <input id="setting-preheader" value={details.preheader} disabled={readOnly} maxLength={300}
                 onInput={(e) => onChange({ preheader: e.currentTarget.value })} />
          <small class="muted">Shown after the subject in most inboxes. One short sentence that makes people open it.</small>
        </label>
        <label class="field" for="setting-name">
          <span>Campaign name</span>
          <input id="setting-name" value={details.name} disabled={readOnly} maxLength={200}
                 onInput={(e) => onChange({ name: e.currentTarget.value })} />
          <small class="muted">Only you see this.</small>
        </label>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Brand & style

interface BrandProps {
  document: CampaignDocument;
  library: SectionLibrary;
  scheme: 'light' | 'dark';
  readOnly: boolean;
  onChange: (document: CampaignDocument) => void;
  onScheme: (scheme: 'light' | 'dark') => void;
}

export function BrandPanel({ document, library, scheme, readOnly, onChange, onScheme }: BrandProps) {
  const brand = document.brand;
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (change: Partial<Brand>) => onChange(updateBrand(document, change));

  const saveAsDefault = async () => {
    setSaved(null);
    setError(null);
    try {
      await api('/settings', 'PATCH', { brand });
      setSaved('Saved. New campaigns will start with this brand.');
    } catch (e) {
      setError(describe(e));
    }
  };

  return (
    <>
      <h2 class="inspector__title">Brand & style</h2>
      <fieldset class="form" disabled={readOnly}>
        <h3 class="panel-title">Style</h3>
        <div class="presets">
          {Object.entries(library.presets).map(([key, preset]) => (
            <button key={key} type="button" class="preset" onClick={() => onChange(applyPreset(document, preset))}>
              <strong>{preset.label}</strong>
              <small>{preset.description}</small>
            </button>
          ))}
        </div>
        <p class="hint">A style changes fonts, spacing and corners. Your colours and logo stay.</p>

        <h3 class="panel-title">Logo</h3>
        <ImageField id="brand-logo" label="Logo" value={brand.logo} onValue={(logo) => set({ logo })} disabled={readOnly}
                    hint="Shown in Header sections that don't have their own image. Works best with a solid background." />

        <h3 class="panel-title">Colours</h3>
        <div class="segmented" role="group" aria-label="Palette">
          {(['light', 'dark'] as const).map((s) => (
            <button key={s} type="button" class={scheme === s ? 'is-active' : ''} onClick={() => onScheme(s)}>
              {s === 'light' ? 'Light' : 'Dark mode'}
            </button>
          ))}
        </div>
        {scheme === 'dark' && <p class="hint">Used by mail apps in dark mode, like Apple Mail and Outlook. Gmail uses its own.</p>}
        <div class="colours">
          {(Object.keys(PALETTE_LABELS) as Array<keyof Palette>).map((key) => (
            <label key={key} class="colour">
              <input type="color" value={brand[scheme][key]}
                     onInput={(e) => onChange(updatePalette(document, scheme, key, e.currentTarget.value))} />
              <span>{PALETTE_LABELS[key]}</span>
            </label>
          ))}
        </div>

        <h3 class="panel-title">Fonts</h3>
        {(['heading', 'body', 'small'] as const).map((key) => (
          <label key={key} class="field">
            <span>{{ heading: 'Headings', body: 'Body text', small: 'Small text' }[key]}</span>
            <select value={brand.fonts[key]} onChange={(e) => set({ fonts: { ...brand.fonts, [key]: e.currentTarget.value } })}>
              {FONT_OPTIONS.map(([stack, label]) => <option key={stack} value={stack}>{label}</option>)}
            </select>
          </label>
        ))}
        <div class="toggles">
          <span>Button text</span>
          <button type="button" class={`toggle${brand.button.bold ? ' is-active' : ''}`} aria-pressed={brand.button.bold}
                  onClick={() => set({ button: { ...brand.button, bold: !brand.button.bold } })}><b>B</b></button>
          <button type="button" class={`toggle${brand.button.italic ? ' is-active' : ''}`} aria-pressed={brand.button.italic}
                  onClick={() => set({ button: { ...brand.button, italic: !brand.button.italic } })}><i>I</i></button>
        </div>

        <h3 class="panel-title">Layout</h3>
        <div class="form-grid">
          {(Object.keys(LAYOUT_LIMITS) as LayoutKey[]).map((key) => {
            const limit = LAYOUT_LIMITS[key];
            return (
              <label key={key} class="field">
                <span>{limit.label}</span>
                <input type="number" min={limit.min} max={limit.max} value={brand[key]}
                       onChange={(e) => {
                         const n = Math.round(Number(e.currentTarget.value));
                         if (Number.isFinite(n)) set({ [key]: Math.min(limit.max, Math.max(limit.min, n)) });
                       }} />
              </label>
            );
          })}
        </div>
      </fieldset>
      {!readOnly && (
        <div class="inspector__footer">
          <Button onClick={() => void saveAsDefault()}>Use as default brand</Button>
          {saved && <Notice tone="success">{saved}</Notice>}
          {error && <Notice tone="error">{error}</Notice>}
        </div>
      )}
    </>
  );
}
