/** Addresses that are never emailed: bounces, spam complaints and ones blocked by hand. */

import { useState } from 'preact/hooks';
import { api, type Suppression } from '../api';
import { Link } from '../router';
import { Button, describe, EmptyState, Field, formatDate, Notice, PageHeader, usePaged, useSubmit } from '../ui';

const REASONS = { bounce: 'Bounced', complaint: 'Marked as spam', manual: 'Blocked by hand' };

export function Suppressions() {
  const items = usePaged<Suppression>('/suppressions');
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const add = useSubmit(async () => {
    await api('/suppressions', 'POST', { email, detail: 'Added in Mail Studio' });
    setEmail('');
    items.reload();
  });
  const lift = (s: Suppression) => {
    if (!confirm(`Allow email to ${s.email} again? They still need to be on a list to get campaigns.`)) return;
    setError(null);
    api(`/suppressions/${encodeURIComponent(s.email)}`, 'DELETE').then(items.reload).catch((e: unknown) => setError(describe(e)));
  };

  return (
    <>
      <p class="crumbs"><Link href="/contacts">← Contacts</Link></p>
      <PageHeader
        title="Addresses never emailed"
        intro="Mail Studio skips these on every list. Bounces and spam complaints are added automatically and stay blocked, which protects your sending reputation."
      />
      <form class="toolbar" onSubmit={add.onSubmit}>
        <Field label="Block an address" type="email" value={email} onValue={setEmail} placeholder="someone@example.com" required />
        <Button type="submit" busy={add.busy}>Block</Button>
      </form>
      {add.error && <Notice tone="error">{add.error}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {items.state === 'error' && <Notice tone="error">{items.error}</Notice>}
      {items.state === 'ready' && !items.items.length && (
        <EmptyState title="None yet"><p>Good news: nothing has bounced or been reported as spam.</p></EmptyState>
      )}
      {items.state === 'ready' && items.items.length > 0 && (
        <>
          <table class="table">
            <thead><tr><th>Address</th><th>Why</th><th>Since</th><th aria-label="Actions" /></tr></thead>
            <tbody>
              {items.items.map((s) => (
                <tr key={s.email}>
                  <td>{s.email}</td>
                  <td>{REASONS[s.reason]}{s.detail && <div class="muted small">{s.detail}</div>}</td>
                  <td>{formatDate(s.created_at)}</td>
                  <td class="table__actions">{s.reason === 'manual' && <Button onClick={() => lift(s)}>Allow again</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.hasMore && <div class="more"><Button busy={items.loadingMore} onClick={items.more}>Show more</Button></div>}
        </>
      )}
    </>
  );
}
