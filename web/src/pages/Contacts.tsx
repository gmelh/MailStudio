/** Everyone you can email: search, filter by list, add one, or import many. */

import { useState } from 'preact/hooks';
import { all, api, type Contact, type MailingList } from '../api';
import { isString, usePref } from '../prefs';
import { Link, navigate } from '../router';
import {
  Button, Checkbox, count, EmptyState, Field, formatDate, fullName, Notice, PageHeader,
  useDebounced, useLoad, usePaged, UserError, useSubmit,
} from '../ui';

export const SUPPRESSION_LABELS: Record<NonNullable<Contact['suppressed']>, string> = {
  bounce: 'Address bounced',
  complaint: 'Marked as spam',
  manual: 'Do not email',
};

export function Contacts() {
  const [search, setSearch] = useState('');
  const [chosenList, setListId] = usePref('contacts-list', '', isString);
  const [adding, setAdding] = useState(false);
  const q = useDebounced(search.trim());
  const [lists] = useLoad(() => all<MailingList>('/lists'));
  // A remembered list that's since been deleted means "Everyone".
  const listId = lists.state === 'ready' && !lists.data.some((l) => l.id === chosenList) ? '' : chosenList;
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (listId) params.set('list_id', listId);
  const contacts = usePaged<Contact>(`/contacts?${params.toString()}`);
  const listOptions = lists.state === 'ready' ? lists.data : [];
  const filtered = Boolean(q || listId);

  return (
    <>
      <PageHeader
        title="Contacts"
        intro="Everyone you can email, and how they joined."
        actions={
          <>
            <Link href="/contacts/import" class="btn btn--plain">Import from a spreadsheet</Link>
            {!adding && <Button variant="primary" onClick={() => setAdding(true)}>Add a contact</Button>}
          </>
        }
      />
      {adding && (
        <AddContact
          lists={listOptions}
          onCancel={() => setAdding(false)}
          onDone={(id) => navigate(`/contacts/${id}`)}
        />
      )}

      <div class="toolbar">
        <input class="search" type="search" placeholder="Search by email or name" aria-label="Search contacts"
               value={search} onInput={(e) => setSearch(e.currentTarget.value)} />
        <select aria-label="Filter by list" value={listId} onChange={(e) => setListId(e.currentTarget.value)}>
          <option value="">Everyone</option>
          {listOptions.map((l) => <option key={l.id} value={l.id}>On “{l.name}”</option>)}
        </select>
        <Link href="/contacts/suppressed" class="toolbar__link">Addresses never emailed</Link>
      </div>

      {contacts.state === 'loading' && <p class="muted">Loading…</p>}
      {contacts.state === 'error' && <Notice tone="error">{contacts.error}</Notice>}
      {contacts.state === 'ready' && !contacts.items.length && (
        filtered ? (
          <EmptyState title="Nobody matches">
            <p>Try a different search, or choose “Everyone”.</p>
          </EmptyState>
        ) : (
          <EmptyState title="No contacts yet">
            <p>Import the people who've agreed to hear from you, or add them one at a time.</p>
            <p><Link href="/contacts/import" class="btn btn--primary">Import from a spreadsheet</Link></p>
          </EmptyState>
        )
      )}
      {contacts.state === 'ready' && contacts.items.length > 0 && (
        <>
          <table class="table table--rows">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Lists</th>
                <th>Added</th>
              </tr>
            </thead>
            <tbody>
              {contacts.items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/contacts/${c.id}`} class="row-link"><strong>{c.email}</strong></Link>
                    {c.suppressed && <span class="badge badge--cancelled">{SUPPRESSION_LABELS[c.suppressed]}</span>}
                    {c.tags.length > 0 && <div class="muted small">{c.tags.join(', ')}</div>}
                  </td>
                  <td>{fullName(c) || '—'}</td>
                  <td>{count(c.lists.filter((m) => m.status === 'subscribed').length, 'list')}</td>
                  <td>{formatDate(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {contacts.hasMore && (
            <div class="more">
              <Button busy={contacts.loadingMore} onClick={contacts.more}>Show more</Button>
            </div>
          )}
        </>
      )}
    </>
  );
}

export const CONSENT_SOURCES: Array<[string, string]> = [
  ['Signed up on my website', 'They signed up on my website'],
  ['Asked in person to join', 'They asked me in person'],
  ['Customer who agreed to receive email', 'They’re a customer who agreed to receive email'],
  ['', 'Something else…'],
];

/** "How did they agree?" — required whenever someone is added to a list. */
export function ConsentPicker({ value, onValue }: { value: string; onValue: (v: string) => void }) {
  const preset = CONSENT_SOURCES.some(([v]) => v && v === value) ? value : '';
  const [other, setOther] = useState(!preset && value !== '');
  return (
    <fieldset class="consent">
      <legend>How did they agree to hear from you?</legend>
      {CONSENT_SOURCES.map(([v, label]) => (
        <label key={label} class="check">
          <input type="radio" name="consent" checked={v ? value === v && !other : other}
                 onChange={() => { setOther(!v); onValue(v); }} />
          <span>{label}</span>
        </label>
      ))}
      {other && (
        <input aria-label="How they agreed" placeholder="For example: entered the prize draw and ticked “Send me news”"
               value={value} onInput={(e) => onValue(e.currentTarget.value)} maxLength={150} />
      )}
      <small class="muted">This is kept with each person, so you can show they agreed if anyone asks.</small>
    </fieldset>
  );
}

function AddContact({ lists, onDone, onCancel }: {
  lists: MailingList[]; onDone: (id: string) => void; onCancel: () => void;
}) {
  const [email, setEmail] = useState('');
  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [chosen, setChosen] = useState<string[]>(lists.length === 1 ? [lists[0]!.id] : []);
  const [consent, setConsent] = useState('');
  const { busy, error, onSubmit } = useSubmit(async () => {
    if (chosen.length && !consent.trim()) throw new UserError('Choose how they agreed to hear from you.');
    const contact = await api<Contact>('/contacts', 'POST', {
      email, first_name: first, last_name: last, lists: chosen,
      consent: chosen.length ? { source: consent.trim() } : undefined,
    });
    onDone(contact.id);
  });
  return (
    <form class="card form-card" onSubmit={onSubmit}>
      <h2>Add a contact</h2>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Email" type="email" value={email} onValue={setEmail} required />
      <div class="form-grid">
        <Field label="First name" value={first} onValue={setFirst} />
        <Field label="Last name" value={last} onValue={setLast} />
      </div>
      {lists.length > 0 && (
        <fieldset class="consent">
          <legend>Lists</legend>
          {lists.map((l) => (
            <Checkbox key={l.id} label={l.name} checked={chosen.includes(l.id)}
                      onChecked={(on) => setChosen((c) => (on ? [...c, l.id] : c.filter((x) => x !== l.id)))} />
          ))}
        </fieldset>
      )}
      {chosen.length > 0 && <ConsentPicker value={consent} onValue={setConsent} />}
      <div class="actions">
        <Button type="submit" variant="primary" busy={busy}>Add contact</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
