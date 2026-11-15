/** Bring in contacts from a spreadsheet saved as CSV. */

import { useState } from 'preact/hooks';
import { all, api, type ImportResult, type MailingList, uploadForm } from '../api';
import { Link } from '../router';
import { Button, count, Field, Loading, Notice, PageHeader, useLoad, UserError, useSubmit } from '../ui';
import { ConsentPicker } from './Contacts';

const ALIASES: Record<string, string> = { 'e-mail': 'email', email_address: 'email', firstname: 'first_name', lastname: 'last_name' };
const headerKey = (raw: string) => {
  const key = raw.trim().toLowerCase().split(/\s+/).join('_');
  return ALIASES[key] ?? key;
};

interface Peek {
  file: File;
  columns: string[];
  rows: string[][];
  lines: number;
}

/** A rough look at the first rows, so people can check they picked the right file. */
async function peek(file: File): Promise<Peek> {
  const text = (await file.slice(0, 64 * 1024).text()).replace(/^﻿/, '');
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const first = lines[0] ?? '';
  const delimiter = [',', ';', '\t'].reduce((best, d) => (first.split(d).length > first.split(best).length ? d : best), ',');
  const split = (line: string) => line.split(delimiter).map((c) => c.trim().replace(/^"(.*)"$/, '$1'));
  return { file, columns: split(first), rows: lines.slice(1, 6).map(split), lines: lines.length - 1 };
}

export function Import() {
  const [lists] = useLoad(() => all<MailingList>('/lists'));
  return (
    <>
      <p class="crumbs"><Link href="/contacts">← Contacts</Link></p>
      <PageHeader title="Import contacts" intro="From a spreadsheet saved as CSV. People already here are updated, never duplicated." />
      <Loading result={lists}>{() => lists.state === 'ready' && <ImportForm lists={lists.data} />}</Loading>
    </>
  );
}

function ImportForm({ lists: initialLists }: { lists: MailingList[] }) {
  const [lists, setLists] = useState(initialLists);
  const [file, setFile] = useState<Peek | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [listId, setListId] = useState(lists[0]?.id ?? 'new');
  const [newList, setNewList] = useState('');
  const [consent, setConsent] = useState('');
  const [tags, setTags] = useState('');
  const [result, setResult] = useState<{ outcome: ImportResult; listName: string } | null>(null);

  const choose = (picked: File | undefined) => {
    setFileError(null);
    setResult(null);
    if (!picked) return setFile(null);
    if (picked.size > 10 * 1024 * 1024) return setFileError('That file is over 10 MB. Split it into smaller files.');
    peek(picked).then(setFile).catch(() => setFileError("Couldn't read that file."));
  };

  const hasEmail = file?.columns.some((c) => headerKey(c) === 'email') ?? false;

  const { busy, error, onSubmit } = useSubmit(async () => {
    if (!file) throw new UserError('Choose a CSV file first.');
    if (!consent.trim()) throw new UserError('Choose how these people agreed to hear from you.');
    let target = listId;
    let listName = lists.find((l) => l.id === listId)?.name ?? '';
    if (listId === 'new') {
      if (!newList.trim()) throw new UserError('Name the new list.');
      const created = await api<MailingList>('/lists', 'POST', { name: newList.trim() });
      target = created.id;
      listName = created.name;
      setLists((known) => [...known, created]);
      setListId(created.id);  // a retry after a failed upload reuses it
    }
    const form = new FormData();
    form.append('file', file.file);
    form.append('consent_source', consent.trim());
    form.append('list_id', target);
    form.append('tags', tags);
    const outcome = await uploadForm<ImportResult>('/contacts/import', form);
    setResult({ outcome, listName });
  });

  if (result) return <Summary {...result} onAgain={() => { setResult(null); setFile(null); }} />;

  return (
    <form class="card form-card" onSubmit={onSubmit}>
      {error && <Notice tone="error">{error}</Notice>}

      <h2>1. Choose the file</h2>
      <label class="dropzone">
        <input type="file" accept=".csv,text/csv,text/plain" onChange={(e) => choose(e.currentTarget.files?.[0])} />
        <span>{file ? <><strong>{file.file.name}</strong> · about {count(file.lines, 'row')}</> : 'Choose a .csv file'}</span>
        <small class="muted">In Excel or Google Sheets: File → Save as / Download → CSV. It needs a column called “email”;
          “first name” and “last name” are used too, and other columns are kept.</small>
      </label>
      {fileError && <Notice tone="error">{fileError}</Notice>}
      {file && !hasEmail && (
        <Notice tone="error">There's no “email” column in the first row. Rename that column's heading to “email” and save again.</Notice>
      )}
      {file && hasEmail && (
        <div class="table-scroll">
          <table class="table table--plain table--compact">
            <thead><tr>{file.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
            <tbody>{file.rows.map((r, i) => <tr key={i}>{file.columns.map((_, j) => <td key={j}>{r[j] ?? ''}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}

      <h2>2. Add them to a list</h2>
      <label class="field" for="import-list">
        <span>List</span>
        <select id="import-list" value={listId} onChange={(e) => setListId(e.currentTarget.value)}>
          {lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          <option value="new">A new list…</option>
        </select>
      </label>
      {listId === 'new' && <Field label="New list name" value={newList} onValue={setNewList} placeholder="Newsletter" />}
      <ConsentPicker value={consent} onValue={setConsent} />
      <Field label="Tags (optional)" value={tags} onValue={setTags} hint="Added to everyone in this file, e.g. “2026 customers”." />

      <div class="actions">
        <Button type="submit" variant="primary" busy={busy} disabled={!file || !hasEmail}>Import</Button>
      </div>
    </form>
  );
}

function Summary({ outcome, listName, onAgain }: { outcome: ImportResult; listName: string; onAgain: () => void }) {
  const skipped = outcome.skipped_unsubscribed + outcome.skipped_suppressed;
  return (
    <section class="card form-card">
      <h2>Import finished</h2>
      <div class="stats">
        <Stat value={outcome.created} label="new contacts" />
        <Stat value={outcome.updated} label="updated" />
        <Stat value={outcome.added_to_list} label={`added to “${listName}”`} />
        <Stat value={skipped} label="left off the list" tone={skipped ? 'warn' : undefined} />
        <Stat value={outcome.invalid} label="rows with problems" tone={outcome.invalid ? 'bad' : undefined} />
      </div>
      {outcome.skipped_unsubscribed > 0 && (
        <p>{count(outcome.skipped_unsubscribed, 'person', 'people')} had unsubscribed from this list before, so they weren't added back.</p>
      )}
      {outcome.skipped_suppressed > 0 && (
        <p>{count(outcome.skipped_suppressed, 'address', 'addresses')} bounced, complained or are blocked, so they're never emailed.</p>
      )}
      {outcome.problems.length > 0 && (
        <details open={outcome.problems.length <= 10}>
          <summary>Rows that couldn't be imported</summary>
          <ul class="problems">
            {outcome.problems.map((p) => <li key={p.row}>Row {p.row}: {p.error}</li>)}
          </ul>
          {outcome.invalid > outcome.problems.length && <p class="muted small">Showing the first {outcome.problems.length}.</p>}
        </details>
      )}
      <div class="actions">
        <Link href="/contacts" class="btn btn--primary">See contacts</Link>
        <Button onClick={onAgain}>Import another file</Button>
      </div>
    </section>
  );
}

export function Stat({ value, label, tone }: { value: number | string; label: string; tone?: 'warn' | 'bad' | 'good' }) {
  return (
    <div class={`stat${tone ? ` stat--${tone}` : ''}`}>
      <strong>{typeof value === 'number' ? value.toLocaleString() : value}</strong>
      <span>{label}</span>
    </div>
  );
}
