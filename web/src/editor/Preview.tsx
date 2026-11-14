/**
 * The email preview: the server renders the campaign exactly as it would be sent,
 * and it's shown in an iframe. Clicking a section in the preview selects it.
 *
 * The iframe runs no scripts (sandboxed). It shares the app's origin so the editor
 * can listen for clicks and outline the selected section inside it.
 */

import { useEffect, useRef, useState } from 'preact/hooks';
import { api, type CampaignDocument, type RenderedEmail } from '../api';
import { describe } from '../ui';
import { cssId } from './model';

const RENDER_DELAY_MS = 250;

interface Props {
  document: CampaignDocument;
  subject: string;
  preheader: string;
  scheme: 'light' | 'dark';
  device: 'desktop' | 'mobile';
  selected: string | null;
  onSelect: (id: string) => void;
}

const EDITOR_STYLES = `
  [class*="ms-s-"] { cursor: pointer; }
  [class*="ms-s-"]:hover { outline: 2px dashed rgba(71, 104, 218, .45); outline-offset: -2px; }
`;

export function Preview({ document, subject, preheader, scheme, device, selected, onSelect }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scroll = useRef(0);
  const ids = useRef(new Map<string, string>());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Re-render shortly after the last change.
  useEffect(() => {
    const timer = setTimeout(() => {
      api<RenderedEmail>('/render', 'POST', { document, subject, preheader, scheme })
        .then((rendered) => {
          scroll.current = frame.current?.contentWindow?.scrollY ?? 0;
          setError(null);
          setHtml(rendered.html);
        })
        .catch((e: unknown) => setError(describe(e)));
    }, RENDER_DELAY_MS);
    return () => clearTimeout(timer);
  }, [document, subject, preheader, scheme]);

  ids.current = new Map(document.sections.map((s) => [cssId(s.id), s.id]));

  // Wire up each freshly loaded preview.
  const onLoad = () => {
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    const style = doc.createElement('style');
    style.id = 'editor-styles';
    doc.head.append(style);
    const marker = doc.createElement('style');
    marker.id = 'editor-selection';
    doc.head.append(marker);
    paintSelection(doc, selected);
    doc.getElementById('editor-styles')!.textContent = EDITOR_STYLES;
    frame.current?.contentWindow?.scrollTo(0, scroll.current);

    doc.addEventListener('click', (event) => {
      const target = event.target as Element | null;
      if (target?.closest('a')) event.preventDefault(); // links never navigate inside the editor
      const sectionEl = target?.closest('[class*="ms-s-"]');
      const token = sectionEl && [...sectionEl.classList].find((c) => c.startsWith('ms-s-'));
      const id = token && ids.current.get(token.slice(5));
      if (id) onSelectRef.current(id);
    });
  };

  useEffect(() => {
    const doc = frame.current?.contentDocument;
    if (doc) paintSelection(doc, selected);
  }, [selected, html]);

  return (
    <div class={`preview preview--${device}`}>
      {error && <div class="notice notice--error preview__error">{error}</div>}
      {html === null && !error && <p class="muted preview__loading">Rendering…</p>}
      {html !== null && (
        <iframe
          ref={frame}
          title="Email preview"
          class="preview__frame"
          sandbox="allow-same-origin"
          srcdoc={html}
          onLoad={onLoad}
        />
      )}
    </div>
  );
}

function paintSelection(doc: Document, selected: string | null): void {
  const marker = doc.getElementById('editor-selection');
  if (!marker) return;
  marker.textContent = selected
    ? `.ms-s-${CSS.escape(cssId(selected))} { outline: 3px solid #4768da !important; outline-offset: -3px; }`
    : '';
  if (selected) {
    const el = doc.querySelector(`.ms-s-${CSS.escape(cssId(selected))}`);
    const box = el?.getBoundingClientRect();
    const view = doc.defaultView;
    if (box && view && (box.top < 0 || box.bottom > view.innerHeight)) {
      el!.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}
