/** One person: their details, the lists they're on, and the record of how they agreed. */

import { useState } from 'preact/hooks';
import { all, api, type ConsentEvent, type Contact, type MailingList } from '../api';
import { Link, navigate } from '../router';
import { Button, describe, Field, formatDate, fullName, Loading, Notice, PageHeader, useLoad, useSubmit } from '../ui';
import { ConsentPicker, SUPPRESSION_LABELS } from './Contacts';

const STATUS_LABELS = { subscribed: 'Subscribed', pending: 'Waiting for confirmation', unsubscribed: 'Unsubscribed' };
const ACTION_LABELS = { consent: 'Agreed to join', confirm: 'Confirmed', unsubscribe: 'Unsubscribed' };

export function ContactDetail({ id }: { id: string }) {
  const [data, reload] = useLoad(
    () => Promise.all([
      api<Contact>(`/contacts/${id}`),
      api<ConsentEvent[]>(`/contacts/${id}/history`),
      all<MailingList>('/lists'),
    ]),
    [id],
  );
  return (
    <>
      <p class="crumbs"><Link href="/contacts">← Contacts</Link></p>
      <Loading result={data}>
        {() => data.state === 'ready' && (
          <Detail key={data.data[0].updated_at} contact={data.data[0]} history={data.data[1]} lists={data.data[2]} reload={reload} />
        )}
      </Loading>
    </>
  );
}

function Detail({ contact, history, lists, reload }: {
  contact: Contact; history: ConsentEvent[]; lists: MailingList[]; reload: () => void;
}) {
  const [email, setEmail] = useState(contact.email);
  const [first, setFirst] = useState(contact.first_name);
  const [last, setLast] = useState(contact.last_name);
  const [tags, setTags] = useState(contact.tags.join(', '));
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState<string | null>(null);

  const save = useSubmit(async () => {
    setSaved(false);
    await api(`/contacts/${contact.id}`, 'PATCH', {
      email, first_name: first, last_name: last,
      tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
    });
    setSaved(true);
    reload();
  });

  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    try {
      await work();
      reload();
    } catch (e) {
      setError(describe(e));
    }
  };

  const unsubscribe = (list: MailingList) => {
    if (!confirm(`Unsubscribe ${contact.email} from “${list.name}”? They won't get its emails, and can't be added back without asking them again.`)) return;
    void act(() => api(`/lists/${list.id}/members/${contact.id}/unsubscribe`, 'POST'));
  };

  const erase = () => {
    if (!confirm(`Erase ${contact.email}? Their details and history are deleted for good. If they unsubscribed or bounced, the address stays blocked so they're never emailed again.`)) return;
    setError(null);
    api(`/contacts/${contact.id}`, 'DELETE').then(() => navigate('/contacts')).catch((e: unknown) => setError(describe(e)));
  };

  const membership = new Map(contact.lists.map((m) => [m.list_id, m.status]));
  const extraFields = Object.entries(contact.fields);

  return (
    <>
      <PageHeader title={fullName(contact) || contact.email} intro={`Added ${formatDate(contact.created_at)}`} />
      {contact.suppressed && (
        <Notice tone="error">
          {SUPPRESSION_LABELS[contact.suppressed]}: this address is never emailed.
          {contact.suppressed === 'manual' && <> You can change that in <Link href="/contacts/suppressed">Addresses never emailed</Link>.</>}
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      <div class="columns">
        <form class="card form-card" onSubmit={save.onSubmit}>
          <h2>Details</h2>
          {save.error && <Notice tone="error">{save.error}</Notice>}
          {saved && <Notice tone="success">Saved.</Notice>}
          <Field label="Email" type="email" value={email} onValue={setEmail} required />
          <div class="form-grid">
            <Field label="First name" value={first} onValue={setFirst} />
            <Field label="Last name" value={last} onValue={setLast} />
          </div>
          <Field label="Tags" value={tags} onValue={setTags} hint="Separate with commas. Use tags to send to part of a list." />
          {extraFields.length > 0 && (
            <dl class="facts">
              {extraFields.map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{String(v ?? '—')}</dd></div>
              ))}
            </dl>
          )}
          <div class="actions">
            <Button type="submit" variant="primary" busy={save.busy}>Save</Button>
          </div>
        </form>

        <section class="card form-card">
          <h2>Lists</h2>
          {!lists.length && <p class="muted">No lists yet. <Link href="/lists">Create one</Link>.</p>}
          <ul class="plain-list">
            {lists.map((list) => {
              const status = membership.get(list.id);
              return (
                <li key={list.id}>
                  <div>
                    <strong>{list.name}</strong>
                    <div class="muted small">{status ? STATUS_LABELS[status] : 'Not on this list'}</div>
                  </div>
                  {status === 'subscribed' || status === 'pending' ? (
                    <Button onClick={() => unsubscribe(list)}>Unsubscribe</Button>
                  ) : status !== 'unsubscribed' && !contact.suppressed ? (
                    <Button onClick={() => setJoining(list.id)}>Add to list</Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {joining && (
            <JoinList
              contact={contact}
              list={lists.find((l) => l.id === joining)!}
              onCancel={() => setJoining(null)}
              onDone={() => { setJoining(null); reload(); }}
            />
          )}
          {contact.lists.some((m) => m.status === 'unsubscribed') && (
            <p class="hint">People who unsubscribed can only rejoin by signing up again themselves.</p>
          )}
        </section>
      </div>

      <section class="card">
        <h2 class="card__title">History</h2>
        {history.length ? (
          <table class="table table--plain">
            <thead><tr><th>When</th><th>What</th><th>List</th><th>How</th></tr></thead>
            <tbody>
              {history.map((e, i) => (
                <tr key={i}>
                  <td>{formatDate(e.created_at)}</td>
                  <td>{ACTION_LABELS[e.action]}</td>
                  <td>{e.list_name ?? (e.list_id ? 'A deleted list' : '—')}</td>
                  <td>{e.source}{e.ip && <span class="muted small"> · {e.ip}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p class="muted">Nothing recorded yet.</p>}
      </section>

      <section class="danger-zone">
        <div>
          <strong>Erase this contact</strong>
          <p class="muted small">For when someone asks you to delete their data.</p>
        </div>
        <Button variant="danger" onClick={erase}>Erase…</Button>
      </section>
    </>
  );
}

function JoinList({ contact, list, onDone, onCancel }: {
  contact: Contact; list: MailingList; onDone: () => void; onCancel: () => void;
}) {
  const [consent, setConsent] = useState('');
  const { busy, error, onSubmit } = useSubmit(async () => {
    await api(`/lists/${list.id}/members`, 'POST', { contact_ids: [contact.id], consent: { source: consent.trim() || 'Added by hand' } });
    onDone();
  });
  return (
    <form class="inset" onSubmit={onSubmit}>
      <strong>Add to “{list.name}”</strong>
      {error && <Notice tone="error">{error}</Notice>}
      <ConsentPicker value={consent} onValue={setConsent} />
      <div class="actions">
        <Button type="submit" variant="primary" busy={busy} disabled={!consent.trim()}>Add</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
