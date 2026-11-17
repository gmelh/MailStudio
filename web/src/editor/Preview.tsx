/**
 * The email preview: the server renders the campaign exactly as it would be sent,
 * and it's shown in an iframe. Clicking a section in the preview selects it.
 *
 * The iframe runs no scripts (sandboxed). It shares the app's origin so the editor
 * can listen for clicks and outline the selected section inside it.
 *
 * The iframe is as tall as the email and doesn't scroll itself: the canvas around it
 * scrolls, so the scrollbar sits at the canvas's edge, next to the right-hand panel,
 * and follows it when the panels are resized.
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
  html, body { overflow: hidden !important; }
  [class*="ms-s-"] { cursor: pointer; }
  [class*="ms-s-"]:hover { outline: 2px dashed rgba(71, 104, 218, .45); outline-offset: -2px; }
`;

export function Preview({ document, subject, preheader, scheme, device, selected, onSelect }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  const resize = useRef<ResizeObserver | null>(null);
  const ids = useRef(new Map<string, string>());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Re-render shortly after the last change.
  useEffect(() => {
    const timer = setTimeout(() => {
      api<RenderedEmail>('/render', 'POST', { document, subject, preheader, scheme })
        .then((rendered) => {
          setError(null);
          setHtml(forScheme(rendered.html));
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

    // Keep the frame as tall as the email: it changes as images load and as the
    // panels are resized (which reflows the email).
    const measure = () => {
      const el = frame.current;
      const borders = el ? el.offsetHeight - el.clientHeight : 0; // the phone outline in mobile view
      setHeight(contentHeight(doc) + borders);
    };
    measure();
    resize.current?.disconnect();
    resize.current = new ResizeObserver(measure);
    resize.current.observe(doc.body);
    for (const image of doc.images) image.addEventListener('load', measure);

    doc.addEventListener('click', (event) => {
      const target = event.target as Element | null;
      if (target?.closest('a')) event.preventDefault(); // links never navigate inside the editor
      const sectionEl = target?.closest('[class*="ms-s-"]');
      const token = sectionEl && [...sectionEl.classList].find((c) => c.startsWith('ms-s-'));
      const id = token && ids.current.get(token.slice(5));
      if (id) onSelectRef.current(id);
    });
  };

  useEffect(() => () => resize.current?.disconnect(), []);

  useEffect(() => {
    const doc = frame.current?.contentDocument;
    if (doc) paintSelection(doc, selected);
  }, [selected, html]);

  // Bring a newly selected section into view (choosing it in the outline, or adding one).
  useEffect(() => {
    if (selected && frame.current) reveal(frame.current, selected);
  }, [selected]);

  return (
    <div class={`preview preview--${device}`}>
      {error && <div class="notice notice--error preview__error">{error}</div>}
      {html === null && !error && <p class="muted preview__loading">Rendering…</p>}
      {html !== null && (
        <iframe
          ref={frame}
          title="Email preview"
          class="preview__frame"
          style={{ colorScheme: scheme, height: height === null ? undefined : `${height}px` }}
          scrolling="no"
          sandbox="allow-same-origin"
          srcdoc={html}
          onLoad={onLoad}
        />
      )}
    </div>
  );
}

/**
 * The light email carries its dark palette in an `@media (prefers-color-scheme: dark)`
 * block, so mail apps in dark mode can switch to it. In the editor that block would follow
 * the computer's (or MailStudio's) dark mode and cover the light palette being edited, so
 * it's switched off: the preview always shows the palette chosen under Brand & style.
 * (The dark preview is rendered with the dark palette directly and has no such block.)
 */
export function forScheme(html: string): string {
  return html
    .replace(/@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)/gi, '@media not all')
    .replace(/<head(\s[^>]*)?>/i, (head) => head + OWN_COLOURS);
}

/**
 * Browsers that darken pages by themselves (Chrome's auto dark mode and the like) would
 * also darken the email, so the preview stops being what readers see. "only light" turns
 * that off; the email's own colours, light or dark palette, do the rest.
 */
const OWN_COLOURS = '<meta name="color-scheme" content="only light">';

function paintSelection(doc: Document, selected: string | null): void {
  const marker = doc.getElementById('editor-selection');
  if (!marker) return;
  marker.textContent = selected
    ? `.ms-s-${CSS.escape(cssId(selected))} { outline: 3px solid #4768da !important; outline-offset: -3px; }`
    : '';
}

/** The email's height: the bottom of its content, which (unlike the page's scroll
 * height) shrinks again when the email gets shorter. */
function contentHeight(doc: Document): number {
  const bottoms = [...doc.body.children].map((el) => el.getBoundingClientRect().bottom);
  return Math.ceil(Math.max(0, ...bottoms));
}

/** Scroll the canvas just enough to show the selected section. */
function reveal(frame: HTMLIFrameElement, selected: string): void {
  const doc = frame.contentDocument;
  const el = doc?.querySelector(`.ms-s-${CSS.escape(cssId(selected))}`);
  const canvas = frame.closest('.editor__canvas');
  if (!el || !canvas) return;
  const view = canvas.getBoundingClientRect();
  const top = frame.getBoundingClientRect().top + el.getBoundingClientRect().top;
  const bottom = top + el.getBoundingClientRect().height;
  const margin = 24;
  if (top < view.top + margin) {
    canvas.scrollBy({ top: top - view.top - margin, behavior: 'smooth' });
  } else if (bottom > view.bottom - margin) {
    // Show its bottom, unless it's taller than the view: then show its top.
    const by = Math.min(bottom - view.bottom + margin, top - view.top - margin);
    canvas.scrollBy({ top: by, behavior: 'smooth' });
  }
}
