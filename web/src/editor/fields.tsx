/** Inputs for section settings, driven by each field's kind in sections.json. */

import type { RefObject } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { upload, type Asset, type FieldDef } from '../api';
import { describe } from '../ui';

const MERGE_TAGS: Array<[tag: string, label: string]> = [
  ['{{first_name|there}}', 'First name (or "there")'],
  ['{{last_name}}', 'Last name'],
  ['{{email}}', 'Email address'],
];

type TextInput = HTMLInputElement | HTMLTextAreaElement;

/** Insert text at the cursor of an input, as if typed. */
function insertAtCursor(input: TextInput, text: string, onValue: (value: string) => void): void {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  const value = input.value.slice(0, start) + text + input.value.slice(end);
  onValue(value);
  requestAnimationFrame(() => {
    input.focus();
    input.setSelectionRange(start + text.length, start + text.length);
  });
}

function MergeTagMenu({ target, onValue }: { target: () => TextInput | null; onValue: (v: string) => void }) {
  return (
    <select
      class="merge-menu"
      aria-label="Insert a personal detail"
      value=""
      onChange={(e) => {
        const input = target();
        if (input && e.currentTarget.value) insertAtCursor(input, e.currentTarget.value, onValue);
        e.currentTarget.value = '';
      }}
    >
      <option value="">Insert personal detail…</option>
      {MERGE_TAGS.map(([tag, label]) => (
        <option key={tag} value={tag}>{label}</option>
      ))}
    </select>
  );
}

interface FieldProps {
  field: FieldDef;
  value: string;
  onValue: (value: string) => void;
  disabled?: boolean;
}

export function SettingField({ field, value, onValue, disabled }: FieldProps) {
  const id = `setting-${field.key}`;
  const ref = useRef<TextInput>(null);

  switch (field.kind) {
    case 'textarea':
      return (
        <div class="field">
          <div class="field__head">
            <label for={id}>{field.label}</label>
            {!disabled && <MergeTagMenu target={() => ref.current} onValue={onValue} />}
          </div>
          <textarea
            id={id}
            ref={ref as RefObject<HTMLTextAreaElement>}
            value={value}
            rows={5}
            disabled={disabled}
            onInput={(e) => onValue(e.currentTarget.value)}
          />
          <small class="muted">**bold**, *italic*, [link](https://…), a blank line between paragraphs, "- " for a list</small>
        </div>
      );
    case 'select':
      return (
        <label class="field" for={id}>
          <span>{field.label}</span>
          <select id={id} value={value} disabled={disabled} onChange={(e) => onValue(e.currentTarget.value)}>
            {(field.options ?? []).map(([option, label]) => (
              <option key={option} value={option}>{label}</option>
            ))}
          </select>
        </label>
      );
    case 'number':
      return (
        <label class="field" for={id}>
          <span>{field.label}</span>
          <input
            id={id}
            type="number"
            min={field.min}
            max={field.max}
            value={value}
            disabled={disabled}
            onInput={(e) => onValue(e.currentTarget.value)}
          />
        </label>
      );
    case 'image':
      return <ImageField id={id} label={field.label} value={value} onValue={onValue} disabled={disabled}
                         hint={field.fallback === 'brand.logo' ? 'Leave empty to use the logo from Brand & style.' : undefined} />;
    case 'url':
      return (
        <label class="field" for={id}>
          <span>{field.label}</span>
          <input
            id={id}
            type="url"
            placeholder="https://…"
            value={value}
            disabled={disabled}
            onInput={(e) => onValue(e.currentTarget.value)}
          />
        </label>
      );
    default:
      return (
        <div class="field">
          <div class="field__head">
            <label for={id}>{field.label}</label>
            {!disabled && <MergeTagMenu target={() => ref.current} onValue={onValue} />}
          </div>
          <input
            id={id}
            ref={ref as RefObject<HTMLInputElement>}
            value={value}
            disabled={disabled}
            onInput={(e) => onValue(e.currentTarget.value)}
          />
        </div>
      );
  }
}

interface ImageFieldProps {
  id: string;
  label: string;
  value: string;
  onValue: (value: string) => void;
  disabled?: boolean;
  hint?: string;
}

/** An image: upload one (it's resized for email), or paste the address of a hosted image. */
export function ImageField({ id, label, value, onValue, disabled, hint }: ImageFieldProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onValue((await upload<Asset>('/assets', file)).url);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div class="field">
      <label for={id}>{label}</label>
      {value && <img class="image-field__thumb" src={value} alt="" />}
      <div class="image-field__row">
        <label class={`btn btn--plain${disabled || busy ? ' is-disabled' : ''}`}>
          {busy ? 'Uploading…' : value ? 'Replace…' : 'Upload…'}
          <input
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            hidden
            disabled={disabled || busy}
            onChange={(e) => {
              void choose(e.currentTarget.files?.[0]);
              e.currentTarget.value = '';
            }}
          />
        </label>
        {value && !disabled && (
          <button type="button" class="btn btn--plain" onClick={() => onValue('')}>Remove</button>
        )}
      </div>
      <input
        id={id}
        type="url"
        placeholder="…or paste an image address (https://…)"
        value={value}
        disabled={disabled}
        onInput={(e) => onValue(e.currentTarget.value)}
      />
      {hint && <small class="muted">{hint}</small>}
      {error && <small class="field__error">{error}</small>}
    </div>
  );
}
