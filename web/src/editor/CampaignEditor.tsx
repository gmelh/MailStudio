/**
 * The email editor: outline on the left, the live preview in the middle, settings
 * on the right. Every change is saved automatically a moment after it's made.
 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { api, type Campaign, type CampaignDocument, type SectionLibrary } from '../api';
import { Link, navigate } from '../router';
import { Button, describe, Notice } from '../ui';
import { BrandPanel, EmailDetails, SectionInspector, type Details } from './Inspector';
import { createSection, duplicate, insertAfter, moveTo, remove, updateSetting } from './model';
import { Outline, type Selection } from './Outline';
import { Preview } from './Preview';

const SAVE_DELAY_MS = 800;
type SaveState = 'saved' | 'pending' | 'saving' | 'error';

export function CampaignEditor({ campaignId }: { campaignId: string }) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [library, setLibrary] = useState<SectionLibrary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api<Campaign>(`/campaigns/${campaignId}`), api<SectionLibrary>('/sections')])
      .then(([c, l]) => {
        setCampaign(c);
        setLibrary(l);
      })
      .catch((e: unknown) => setLoadError(describe(e)));
  }, [campaignId]);

  if (loadError) {
    return (
      <div class="editor-message">
        <Notice tone="error">{loadError}</Notice>
        <Link href="/campaigns">← Back to campaigns</Link>
      </div>
    );
  }
  if (!campaign || !library) return <p class="editor-message muted">Opening the editor…</p>;
  return <Editor initial={campaign} library={library} />;
}

function Editor({ initial, library }: { initial: Campaign; library: SectionLibrary }) {
  const readOnly = initial.status !== 'draft';
  const [document, setDocument] = useState<CampaignDocument>(initial.document);
  const [details, setDetails] = useState<Details>({
    name: initial.name, subject: initial.subject, preheader: initial.preheader,
  });
  const [selection, setSelection] = useState<Selection>(initial.document.sections[0]?.id ?? 'email');
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop');
  const [scheme, setScheme] = useState<'light' | 'dark'>('light');
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [saveError, setSaveError] = useState<string | null>(null);
  const firstRender = useRef(true);

  // Autosave a moment after the last change.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const save = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setSaveState('saving');
    try {
      await api(`/campaigns/${initial.id}`, 'PATCH', { ...details, name: details.name || 'Untitled campaign', document });
      setSaveState('saved');
      setSaveError(null);
    } catch (e) {
      setSaveState('error');
      setSaveError(describe(e));
      throw e;
    }
  }, [document, details, initial.id]);

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (readOnly) return;
    setSaveState('pending');
    timer.current = setTimeout(() => void save().catch(() => undefined), SAVE_DELAY_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [save, readOnly]);

  const goSend = () => {
    const go = () => navigate(`/campaigns/${initial.id}/send`);
    if (saveState === 'saved') go();
    else void save().then(go).catch(() => undefined);
  };

  // Warn before leaving with unsaved changes.
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (saveState === 'pending' || saveState === 'saving') event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [saveState]);

  const selectedSection = selection !== 'email' && selection !== 'brand' ? selection : null;

  const addSection = useCallback((type: string) => {
    const definition = library.sections[type];
    if (!definition) return;
    const section = createSection(type, definition);
    setDocument((doc) => insertAfter(doc, section, selectedSection));
    setSelection(section.id);
  }, [library, selectedSection]);

  const moveBy = (delta: -1 | 1) => {
    if (!selectedSection) return;
    setDocument((doc) => moveTo(doc, selectedSection, doc.sections.findIndex((s) => s.id === selectedSection) + delta));
  };

  return (
    <div class="editor">
      <header class="editor__bar">
        <Link href="/campaigns" class="editor__back">← Campaigns</Link>
        <div class="editor__title">
          <strong>{details.name || 'Untitled campaign'}</strong>
          <SaveStatus state={readOnly ? 'readonly' : saveState} />
        </div>
        <div class="editor__controls">
          <div class="segmented" role="group" aria-label="Preview width">
            <button type="button" class={device === 'desktop' ? 'is-active' : ''} onClick={() => setDevice('desktop')}>Desktop</button>
            <button type="button" class={device === 'mobile' ? 'is-active' : ''} onClick={() => setDevice('mobile')}>Mobile</button>
          </div>
          <div class="segmented" role="group" aria-label="Preview colours">
            <button type="button" class={scheme === 'light' ? 'is-active' : ''} onClick={() => setScheme('light')}>Light</button>
            <button type="button" class={scheme === 'dark' ? 'is-active' : ''} onClick={() => setScheme('dark')}>Dark</button>
          </div>
          {readOnly
            ? <Link href={`/campaigns/${initial.id}/report`} class="btn btn--plain">Report</Link>
            : <Button variant="primary" onClick={goSend}>Send…</Button>}
        </div>
      </header>

      {readOnly && (
        <div class="editor__notice">
          <Notice>This campaign is {initial.status}, so it can't be changed. You're seeing what was sent.</Notice>
        </div>
      )}
      {saveError && (
        <div class="editor__notice">
          <Notice tone="error">Couldn't save: {saveError}</Notice>
        </div>
      )}

      <div class="editor__body">
        <aside class="editor__panel editor__panel--left">
          <Outline
            document={document}
            library={library}
            selection={selection}
            readOnly={readOnly}
            onSelect={setSelection}
            onAdd={addSection}
            onMove={(id, index) => setDocument((doc) => moveTo(doc, id, index))}
          />
        </aside>

        <main class="editor__canvas" style={{ background: document.brand[scheme].outer }}>
          <Preview
            document={document}
            subject={details.subject}
            preheader={details.preheader}
            scheme={scheme}
            device={device}
            selected={selectedSection}
            onSelect={setSelection}
          />
        </main>

        <aside class="editor__panel editor__panel--right">
          {selection === 'email' && (
            <EmailDetails details={details} readOnly={readOnly} onChange={(change) => setDetails((d) => ({ ...d, ...change }))} />
          )}
          {selection === 'brand' && (
            <BrandPanel document={document} library={library} scheme={scheme} readOnly={readOnly}
                        onChange={setDocument} onScheme={setScheme} />
          )}
          {selectedSection && (
            <SectionInspector
              document={document}
              library={library}
              sectionId={selectedSection}
              readOnly={readOnly}
              onSetting={(key, value) => setDocument((doc) => updateSetting(doc, selectedSection, key, value))}
              onMove={moveBy}
              onDuplicate={() => {
                const [next, copyId] = duplicate(document, selectedSection);
                setDocument(next);
                setSelection(copyId);
              }}
              onDelete={() => {
                const index = document.sections.findIndex((s) => s.id === selectedSection);
                const next = remove(document, selectedSection);
                setDocument(next);
                setSelection(next.sections[Math.min(index, next.sections.length - 1)]?.id ?? 'email');
              }}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function SaveStatus({ state }: { state: SaveState | 'readonly' }) {
  const text = { saved: 'All changes saved', pending: 'Unsaved changes', saving: 'Saving…', error: 'Not saved', readonly: 'Read only' }[state];
  return <span class={`save-status save-status--${state}`} role="status">{text}</span>;
}
