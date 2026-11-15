/** One list: who's on it, and its name. */

import { useState } from 'preact/hooks';
import { api, type MailingList, type Member, type MemberStatus } from '../api';
import { oneOf, usePref } from '../prefs';
import { Link, navigate } from '../router';
import { SignupFormCard } from './SignupFormCard';
import {
  Button, count, describe, EmptyState, Field, formatDate, fullName, Loading, Notice, PageHeader, useLoad, usePaged, useSubmit,
} from '../ui';

const TABS: Array<[MemberStatus, string]> = [
  ['subscribed', 'Subscribed'],
  ['pending', 'Waiting for confirmation'],
  ['unsubscribed', 'Unsubscribed'],
];

export function ListDetail({ id }: { id: string }) {
  const [list, reload] = useLoad(() => api<MailingList>(`/lists/${id}`), [id]);
  return (
    <>
      <p class="crumbs"><Link href="/lists">← Lists</Link></p>
      <Loading result={list}>{() => list.state === 'ready' && <Detail list={list.data} reload={reload} />}</Loading>
    </>
  );
}

function Detail({ list, reload }: { list: MailingList; reload: () => void }) {
  const [tab, setTab] = usePref<MemberStatus>('list-tab', 'subscribed', oneOf('subscribed', 'pending', 'unsubscribed'));
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const members = usePaged<Member>(`/lists/${list.id}/members?status=${tab}`);

  const unsubscribe = (m: Member) => {
    if (!confirm(`Unsubscribe ${m.email} from “${list.name}”?`)) return;
    setError(null);
    api(`/lists/${list.id}/members/${m.id}/unsubscribe`, 'POST')
      .then(() => { members.reload(); reload(); })
      .catch((e: unknown) => setError(describe(e)));
  };

  const remove = () => {
    if (!confirm(`Delete the list “${list.name}”? The contacts stay; they're just no longer grouped in this list. Campaigns already sent aren't affected.`)) return;
    setError(null);
    api(`/lists/${list.id}`, 'DELETE').then(() => navigate('/lists')).catch((e: unknown) => setError(describe(e)));
  };

  return (
    <>
      <PageHeader
        title={list.name}
        intro={list.description || `${count(list.counts.subscribed, 'person', 'people')} subscribed`}
        actions={
          <>
            <Link href="/contacts/import" class="btn btn--plain">Import contacts</Link>
            {!editing && <Button onClick={() => setEditing(true)}>Rename</Button>}
          </>
        }
      />
      {editing && <EditList list={list} onDone={() => { setEditing(false); reload(); }} onCancel={() => setEditing(false)} />}
      {error && <Notice tone="error">{error}</Notice>}

      <div class="tabs" role="tablist">
        {TABS.map(([status, label]) => (
          <button key={status} type="button" role="tab" aria-selected={tab === status}
                  class={tab === status ? 'is-active' : ''} onClick={() => setTab(status)}>
            {label} <span class="tabs__count">{list.counts[status].toLocaleString()}</span>
          </button>
        ))}
      </div>

      {members.state === 'error' && <Notice tone="error">{members.error}</Notice>}
      {members.state === 'loading' && <p class="muted">Loading…</p>}
      {members.state === 'ready' && !members.items.length && (
        <EmptyState title={tab === 'subscribed' ? 'Nobody on this list yet' : 'Nobody here'}>
          {tab === 'subscribed' && <p>Import a spreadsheet, or add people from their contact page.</p>}
        </EmptyState>
      )}
      {members.state === 'ready' && members.items.length > 0 && (
        <>
          <table class="table">
            <thead>
              <tr>
                <th>Email</th><th>Name</th><th>{tab === 'unsubscribed' ? 'Unsubscribed' : 'Joined'}</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {members.items.map((m) => (
                <tr key={m.id}>
                  <td><Link href={`/contacts/${m.id}`} class="row-link">{m.email}</Link></td>
                  <td>{fullName(m) || '—'}</td>
                  <td>{formatDate(tab === 'unsubscribed' ? m.unsubscribed_at : m.subscribed_at)}</td>
                  <td class="table__actions">{tab !== 'unsubscribed' && <Button onClick={() => unsubscribe(m)}>Unsubscribe</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {members.hasMore && <div class="more"><Button busy={members.loadingMore} onClick={members.more}>Show more</Button></div>}
        </>
      )}

      {tab === 'pending' && members.items.length > 0 && (
        <p class="hint">These people signed up but haven't clicked the link in the confirmation email yet. They aren't emailed until they do; links expire after 14 days.</p>
      )}

      <SignupFormCard key={list.updated_at} list={list} onSaved={reload} />

      <section class="danger-zone">
        <div>
          <strong>Delete this list</strong>
          <p class="muted small">Contacts aren't deleted.</p>
        </div>
        <Button variant="danger" onClick={remove}>Delete list…</Button>
      </section>
    </>
  );
}

function EditList({ list, onDone, onCancel }: { list: MailingList; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(list.name);
  const [description, setDescription] = useState(list.description);
  const { busy, error, onSubmit } = useSubmit(async () => {
    await api(`/lists/${list.id}`, 'PATCH', { name, description });
    onDone();
  });
  return (
    <form class="card form-card" onSubmit={onSubmit}>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Name" value={name} onValue={setName} required />
      <Field label="Description" value={description} onValue={setDescription} />
      <div class="actions">
        <Button type="submit" variant="primary" busy={busy}>Save</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
