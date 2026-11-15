/**
 * "New campaign": choose a template, or start blank.
 *
 * Templates come with the app (GET /templates), from the online gallery at
 * mailstudio.email (GET /templates/gallery, fetched by the server), or from a template
 * file someone has (checked by POST /templates/check). Every card is a live preview,
 * rendered by the server with this install's logo and business details, in the colours
 * chosen: the template's own, or the brand colours from Settings.
 */
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import {
  api,
  type Campaign,
  type EmailTemplate,
  type GalleryEntry,
  type RenderedEmail,
  type TemplateCategory,
  type TemplateColours,
  type TemplateList,
} from '../api';
import { forScheme } from '../editor/Preview';
import { oneOf, usePref } from '../prefs';
import { navigate } from '../router';
import { Button, describe, Notice } from '../ui';

const CATEGORY_LABELS: Record<TemplateCategory, string> = {
  newsletter: 'Newsletters',
  promotion: 'Sales & offers',
  event: 'Events',
  announcement: 'Announcements',
  welcome: 'Welcome',
  other: 'Other',
};

type Source = 'included' | 'gallery';
type Choice = { kind: 'blank' } | { kind: 'template'; template: () => Promise<EmailTemplate>; name: string; key: string };

interface Card {
  key: string;
  name: string;
  description: string;
  category: TemplateCategory;
  load: () => Promise<EmailTemplate>;
}

const once = <T,>(work: () => Promise<T>): (() => Promise<T>) => {
  let result: Promise<T> | null = null;
  return () => (result ??= work().catch((e: unknown) => { result = null; throw e; }));
};

export function TemplatePicker({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<TemplateList | null>(null);
  const [gallery, setGallery] = useState<GalleryEntry[] | null>(null);
  const [galleryError, setGalleryError] = useState<string | null>(null);
  const [imported, setImported] = useState<EmailTemplate[]>([]);
  const [source, setSource] = useState<Source>('included');
  const [category, setCategory] = useState<TemplateCategory | 'all'>('all');
  const [colours, setColours] = usePref('templateColours', 'template', oneOf('template', 'brand'));
  const [scheme, setScheme] = useState<'light' | 'dark'>('light');
  const [choice, setChoice] = useState<Choice>({ kind: 'blank' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<TemplateList>('/templates').then(setList, (e: unknown) => setError(describe(e)));
  }, []);

  useEffect(() => {
    if (source !== 'gallery' || gallery) return;
    setGalleryError(null);
    api<{ templates: GalleryEntry[] }>('/templates/gallery').then(
      (g) => setGallery(g.templates),
      (e: unknown) => setGalleryError(describe(e)),
    );
  }, [source, gallery]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', onKey);
    dialog.current?.focus();
    return () => removeEventListener('keydown', onKey);
  }, [onClose]);

  const cards: Card[] = useMemo(() => {
    if (source === 'gallery') {
      return (gallery ?? []).map((g) => ({
        key: `gallery:${g.id}`,
        name: g.name,
        description: g.description,
        category: g.category,
        load: once(() => api<EmailTemplate>(`/templates/gallery/${encodeURIComponent(g.id)}`)),
      }));
    }
    return [...imported, ...(list?.templates ?? [])].map((t, i) => ({
      key: i < imported.length ? `file:${i}:${t.id}` : `app:${t.id}`,
      name: t.name,
      description: t.description,
      category: t.category,
      load: () => Promise.resolve(t),
    }));
  }, [source, gallery, list, imported]);

  const categories = [...new Set(cards.map((c) => c.category))];
  const shown = cards.filter((c) => category === 'all' || c.category === category);

  const importFile = async (picked: File | undefined) => {
    if (!picked) return;
    setError(null);
    try {
      const raw: unknown = JSON.parse(await picked.text());
      const template = await api<EmailTemplate>('/templates/check', 'POST', raw);
      setImported((list) => [template, ...list]);
      setSource('included');
      setCategory('all');
      setChoice({ kind: 'template', template: () => Promise.resolve(template), name: template.name, key: `file:0:${template.id}` });
    } catch (e) {
      setError(e instanceof SyntaxError ? `${picked.name} isn't a template file.` : describe(e));
    } finally {
      if (file.current) file.current.value = '';
    }
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = choice.kind === 'blank'
        ? { name: 'Untitled campaign' }
        : await choice.template().then((template) => ({ name: template.name, template, colours }));
      const campaign = await api<Campaign>('/campaigns', 'POST', body);
      navigate(`/campaigns/${campaign.id}`);
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };

  const chosenKey = choice.kind === 'blank' ? 'blank' : choice.key;

  return (
    <div class="picker-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="picker" role="dialog" aria-modal="true" aria-labelledby="picker-title" tabIndex={-1} ref={dialog}>
        <header class="picker__head">
          <div>
            <h2 id="picker-title">New campaign</h2>
            <p class="muted small">Start from a template, then change anything: words, sections, colours and fonts.</p>
          </div>
          <button type="button" class="picker__close" aria-label="Close" onClick={onClose}>×</button>
        </header>

        <div class="picker__toolbar">
          <div class="segmented" role="tablist" aria-label="Where templates come from">
            <button type="button" role="tab" aria-selected={source === 'included'} class={source === 'included' ? 'is-active' : ''}
                    onClick={() => { setSource('included'); setCategory('all'); }}>Included</button>
            {list?.gallery && (
              <button type="button" role="tab" aria-selected={source === 'gallery'} class={source === 'gallery' ? 'is-active' : ''}
                      onClick={() => { setSource('gallery'); setCategory('all'); }}>mailstudio.email gallery</button>
            )}
          </div>
          <div class="picker__filters">
            {categories.length > 1 && (
              <select aria-label="Kind of email" value={category} onChange={(e) => setCategory(e.currentTarget.value as TemplateCategory | 'all')}>
                <option value="all">All kinds</option>
                {categories.map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
              </select>
            )}
            <div class="segmented" role="group" aria-label="Colours" title="Whose colours the email starts with. You can change them later.">
              <button type="button" class={colours === 'template' ? 'is-active' : ''} aria-pressed={colours === 'template'}
                      onClick={() => setColours('template')}>Template colours</button>
              <button type="button" class={colours === 'brand' ? 'is-active' : ''} aria-pressed={colours === 'brand'}
                      onClick={() => setColours('brand')}>My brand colours</button>
            </div>
            <div class="segmented" role="group" aria-label="Preview in light or dark mode">
              <button type="button" class={scheme === 'light' ? 'is-active' : ''} aria-pressed={scheme === 'light'} onClick={() => setScheme('light')}>Light</button>
              <button type="button" class={scheme === 'dark' ? 'is-active' : ''} aria-pressed={scheme === 'dark'} onClick={() => setScheme('dark')}>Dark</button>
            </div>
          </div>
        </div>

        {error && <div class="picker__notice"><Notice tone="error">{error}</Notice></div>}

        <div class="picker__grid">
          {source === 'included' && (
            <button type="button" class={`tcard tcard--blank${chosenKey === 'blank' ? ' is-chosen' : ''}`}
                    onClick={() => setChoice({ kind: 'blank' })} onDblClick={() => void start()}>
              <span class="tcard__thumb tcard__thumb--blank"><span>+</span></span>
              <span class="tcard__name">Blank</span>
              <span class="tcard__desc">Your brand from Settings, with a headline, some text, a button and your footer.</span>
            </button>
          )}
          {source === 'included' && !list && !error && <p class="muted">Loading templates…</p>}
          {source === 'gallery' && galleryError && (
            <Notice tone="error">
              {galleryError} You can still download template files from mailstudio.email and choose
              “Open a template file” below.
            </Notice>
          )}
          {source === 'gallery' && !gallery && !galleryError && <p class="muted">Fetching the gallery…</p>}
          {source === 'gallery' && gallery?.length === 0 && <p class="muted">The gallery has no templates yet.</p>}
          {shown.map((card) => (
            <TemplateCard
              key={card.key}
              card={card}
              colours={colours}
              scheme={scheme}
              chosen={chosenKey === card.key}
              onChoose={() => setChoice({ kind: 'template', template: card.load, name: card.name, key: card.key })}
              onUse={() => void start()}
            />
          ))}
        </div>

        <footer class="picker__foot">
          <input ref={file} type="file" accept=".json,application/json" hidden onChange={(e) => void importFile(e.currentTarget.files?.[0])} />
          <Button onClick={() => file.current?.click()}>Open a template file…</Button>
          <span class="picker__spacer" />
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} onClick={() => void start()}>
            {choice.kind === 'blank' ? 'Start blank' : `Use ${choice.name}`}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function TemplateCard({ card, colours, scheme, chosen, onChoose, onUse }: {
  card: Card;
  colours: TemplateColours;
  scheme: 'light' | 'dark';
  chosen: boolean;
  onChoose: () => void;
  onUse: () => void;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const thumb = useRef<HTMLSpanElement>(null);

  // Scale the email (680px wide in its frame) to the card's width.
  useEffect(() => {
    const el = thumb.current;
    if (!el) return;
    const fit = () => el.style.setProperty('--scale', String(el.clientWidth / 680));
    const watcher = new ResizeObserver(fit);
    watcher.observe(el);
    fit();
    return () => watcher.disconnect();
  }, []);

  useEffect(() => {
    let live = true;
    setFailed(null);
    card.load()
      .then((template) => api<RenderedEmail>('/templates/preview', 'POST', { template, colours, scheme }))
      .then((r) => { if (live) setHtml(forScheme(r.html)); })
      .catch((e: unknown) => { if (live) setFailed(describe(e)); });
    return () => { live = false; };
  }, [card, colours, scheme]);

  return (
    <button type="button" class={`tcard${chosen ? ' is-chosen' : ''}`} onClick={onChoose} onDblClick={onUse}
            aria-pressed={chosen} title={`${card.name}: double-click to use it`}>
      <span class="tcard__thumb" ref={thumb}>
        {html !== null && (
          <iframe class="tcard__frame" title={`${card.name} preview`} tabIndex={-1} aria-hidden="true"
                  sandbox="allow-same-origin" scrolling="no" srcdoc={html} style={{ colorScheme: scheme }} />
        )}
        {html === null && !failed && <span class="muted small tcard__wait">Rendering…</span>}
        {failed && <span class="small tcard__wait tcard__fail">{failed}</span>}
      </span>
      <span class="tcard__name">{card.name} <span class="tcard__kind">{CATEGORY_LABELS[card.category]}</span></span>
      <span class="tcard__desc">{card.description}</span>
    </button>
  );
}
