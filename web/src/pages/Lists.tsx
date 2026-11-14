/** Groups of people who agreed to hear from you. */

import { useState } from 'preact/hooks';
import { all, api, type MailingList } from '../api';
import { Link, navigate } from '../router';
import { Button, count, EmptyState, Field, Loading, Notice, PageHeader, useLoad, useSubmit } from '../ui';

export function Lists() {
  const [lists] = useLoad(() => all<MailingList>('/lists'));
  const [adding, setAdding] = useState(false);
  return (
    <>
      <PageHeader
        title="Lists"
        intro="Groups of people who agreed to hear from you. Most businesses need just one."
        actions={!adding && <Button variant="primary" onClick={() => setAdding(true)}>New list</Button>}
      />
      {adding && <NewList onCancel={() => setAdding(false)} />}
      <Loading result={lists}>
        {() =>
          lists.state === 'ready' && lists.data.length ? (
            <table class="table">
              <thead>
                <tr><th>List</th><th>Subscribed</th><th>Unsubscribed</th></tr>
              </thead>
              <tbody>
                {lists.data.map((l) => (
                  <tr key={l.id}>
                    <td>
                      <Link href={`/lists/${l.id}`} class="row-link"><strong>{l.name}</strong></Link>
                      {l.description && <div class="muted small">{l.description}</div>}
                    </td>
                    <td>{count(l.counts.subscribed, 'person', 'people')}</td>
                    <td>{l.counts.unsubscribed.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            !adding && (
              <EmptyState title="No lists yet">
                <p>Create a list, then import the people on it.</p>
                <p><Button variant="primary" onClick={() => setAdding(true)}>New list</Button></p>
              </EmptyState>
            )
          )
        }
      </Loading>
    </>
  );
}

function NewList({ onCancel }: { onCancel: () => void }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const { busy, error, onSubmit } = useSubmit(async () => {
    const list = await api<MailingList>('/lists', 'POST', { name, description });
    navigate(`/lists/${list.id}`);
  });
  return (
    <form class="card form-card" onSubmit={onSubmit}>
      <h2>New list</h2>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Name" value={name} onValue={setName} placeholder="Newsletter" required />
      <Field label="Description (optional)" value={description} onValue={setDescription}
             hint="For you, e.g. “Customers who asked for our monthly news”." />
      <div class="actions">
        <Button type="submit" variant="primary" busy={busy}>Create list</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
