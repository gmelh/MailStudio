/**
 * Left panel: the email's details and brand, its sections in order (select,
 * drag to reorder, or use the buttons), and the library of sections to add.
 */

import { useState } from 'preact/hooks';
import type { CampaignDocument, SectionLibrary } from '../api';
import { summary } from './model';

export type Selection = 'email' | 'brand' | string;

interface Props {
  document: CampaignDocument;
  library: SectionLibrary;
  selection: Selection;
  readOnly: boolean;
  onSelect: (selection: Selection) => void;
  onAdd: (type: string) => void;
  onMove: (id: string, index: number) => void;
}

export function Outline({ document, library, selection, readOnly, onSelect, onAdd, onMove }: Props) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  const drop = () => {
    if (dragging !== null && dropIndex !== null) {
      const from = document.sections.findIndex((s) => s.id === dragging);
      onMove(dragging, from < dropIndex ? dropIndex - 1 : dropIndex);
    }
    setDragging(null);
    setDropIndex(null);
  };

  return (
    <div class="outline">
      <div class="outline__group">
        <button type="button" class={`outline__item${selection === 'email' ? ' is-selected' : ''}`}
                onClick={() => onSelect('email')}>
          <span class="outline__icon">✉</span>
          <span><strong>Email details</strong><small>Subject, preview text, name</small></span>
        </button>
        <button type="button" class={`outline__item${selection === 'brand' ? ' is-selected' : ''}`}
                onClick={() => onSelect('brand')}>
          <span class="outline__icon outline__icon--text">Aa</span>
          <span><strong>Brand & style</strong><small>Colours, fonts, logo, presets</small></span>
        </button>
      </div>

      <h2 class="panel-title">Sections</h2>
      <ol class="outline__list" onDragOver={(e) => e.preventDefault()} onDrop={drop}>
        {document.sections.map((section, index) => {
          const definition = library.sections[section.type];
          return (
            <li
              key={section.id}
              class={`${dropIndex === index ? 'drop-before ' : ''}${dropIndex === index + 1 && index === document.sections.length - 1 ? 'drop-after' : ''}`}
              draggable={!readOnly}
              onDragStart={(e) => {
                setDragging(section.id);
                e.dataTransfer?.setData('text/plain', section.id);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setDropIndex(e.clientY > box.top + box.height / 2 ? index + 1 : index);
              }}
              onDragEnd={() => {
                setDragging(null);
                setDropIndex(null);
              }}
            >
              <button
                type="button"
                class={`outline__item${selection === section.id ? ' is-selected' : ''}${dragging === section.id ? ' is-dragging' : ''}`}
                onClick={() => onSelect(section.id)}
              >
                <span class="outline__icon">{definition?.icon ?? '?'}</span>
                <span>
                  <strong>{definition?.label ?? section.type}</strong>
                  <small>{summary(section, definition) || definition?.label}</small>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {!document.sections.length && <p class="muted small">No sections yet. Add one below.</p>}

      {!readOnly && (
        <>
          <h2 class="panel-title">Add a section</h2>
          <div class="library">
            {Object.entries(library.sections).map(([type, definition]) => (
              <button key={type} type="button" class="tile" onClick={() => onAdd(type)}
                      title={`Add ${definition.label} below the selected section`}>
                <span class="tile__icon">{definition.icon}</span>
                {definition.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
