import { useState } from 'preact/hooks';
import { api, type Campaign, type CampaignSummary } from '../api';
import { Link, navigate } from '../router';
import { Button, count, describe, EmptyState, formatDate, Notice, PageHeader, usePaged } from '../ui';
import { TemplatePicker } from './TemplatePicker';

export const STATUS_LABELS: Record<CampaignSummary['status'], string> = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  sending: 'Sending',
  paused: 'Paused',
  sent: 'Sent',
  cancelled: 'Stopped',
};

export const campaignHref = (c: CampaignSummary) => (c.status === 'draft' ? `/campaigns/${c.id}` : `/campaigns/${c.id}/report`);

export function Campaigns() {
  const campaigns = usePaged<CampaignSummary>('/campaigns');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };
  const copy = (c: CampaignSummary) => run(async () => {
    const campaign = await api<Campaign>(`/campaigns/${c.id}/duplicate`, 'POST');
    navigate(`/campaigns/${campaign.id}`);
  });
  const remove = (c: CampaignSummary) => {
    if (!confirm(`Delete the draft “${c.name}”?`)) return;
    void run(async () => {
      await api(`/campaigns/${c.id}`, 'DELETE');
      campaigns.reload();
    });
  };
  const newButton = <Button variant="primary" busy={busy} onClick={() => setPicking(true)}>New campaign</Button>;

  return (
    <>
      <PageHeader title="Campaigns" intro="Newsletters and announcements you've written or sent." actions={newButton} />
      {picking && <TemplatePicker onClose={() => setPicking(false)} />}
      {error && <Notice tone="error">{error}</Notice>}
      {campaigns.state === 'loading' && <p class="muted">Loading…</p>}
      {campaigns.state === 'error' && <Notice tone="error">{campaigns.error}</Notice>}
      {campaigns.state === 'ready' && !campaigns.items.length && (
        <EmptyState title="No campaigns yet">
          <p>Write your first email: pick sections, add your words and photos, and see it as your readers will.</p>
          {newButton}
        </EmptyState>
      )}
      {campaigns.state === 'ready' && campaigns.items.length > 0 && (
        <>
          <table class="table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Status</th>
                <th>Audience</th>
                <th>When</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {campaigns.items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={campaignHref(c)} class="row-link"><strong>{c.name}</strong></Link>
                    <div class="muted small">{c.subject || 'No subject yet'}</div>
                  </td>
                  <td><span class={`badge badge--${c.status}`}>{STATUS_LABELS[c.status]}</span></td>
                  <td>{count(c.audience, 'person', 'people')}</td>
                  <td>
                    {c.status === 'scheduled' ? formatDate(c.scheduled_at) : formatDate(c.sent_at ?? c.updated_at)}
                    <div class="muted small">{c.status === 'scheduled' ? 'Scheduled' : c.sent_at ? 'Sent' : 'Last change'}</div>
                  </td>
                  <td class="table__actions">
                    <span class="row-actions">
                      {c.status === 'draft' && <Link href={`/campaigns/${c.id}/send`} class="btn btn--plain">Send…</Link>}
                      {c.status !== 'draft' && <Link href={`/campaigns/${c.id}/report`} class="btn btn--plain">Report</Link>}
                      <Button onClick={() => void copy(c)}>Copy</Button>
                      {c.status === 'draft' && <Button variant="danger" onClick={() => remove(c)}>Delete</Button>}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {campaigns.hasMore && <div class="more"><Button busy={campaigns.loadingMore} onClick={campaigns.more}>Show more</Button></div>}
        </>
      )}
    </>
  );
}
