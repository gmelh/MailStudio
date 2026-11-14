/** How a scheduled, sending or sent campaign is doing, with the controls that apply. */

import { useEffect, useState } from 'preact/hooks';
import { api, type Campaign, type CampaignStats, type RenderedEmail } from '../api';
import { Link, navigate } from '../router';
import { Button, count, describe, formatDate, Loading, Notice, PageHeader, useLoad } from '../ui';
import { Stat } from './Import';
import { STATUS_LABELS } from './Campaigns';

const LIVE = new Set(['scheduled', 'sending', 'paused']);

export function CampaignReport({ id }: { id: string }) {
  const [data, reload] = useLoad(
    () => Promise.all([api<Campaign>(`/campaigns/${id}`), api<CampaignStats>(`/campaigns/${id}/stats`)]),
    [id],
  );
  const live = data.state === 'ready' && LIVE.has(data.data[0].status);

  // Refresh while it's going out (quietly: keep showing the last numbers).
  const [fresh, setFresh] = useState<[Campaign, CampaignStats] | null>(null);
  useEffect(() => {
    setFresh(null);
    if (!live) return;
    const timer = setInterval(() => {
      Promise.all([api<Campaign>(`/campaigns/${id}`), api<CampaignStats>(`/campaigns/${id}/stats`)])
        .then(setFresh)
        .catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [id, live]);
  useEffect(() => {
    if (fresh && data.state === 'ready' && fresh[0].status !== data.data[0].status) reload();
  }, [fresh]);

  return (
    <>
      <p class="crumbs"><Link href="/campaigns">← Campaigns</Link></p>
      <Loading result={data}>
        {() => data.state === 'ready' && (
          <Report campaign={(fresh ?? data.data)[0]} stats={(fresh ?? data.data)[1]} reload={reload} />
        )}
      </Loading>
    </>
  );
}

function Report({ campaign, stats, reload }: { campaign: Campaign; stats: CampaignStats; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showEmail, setShowEmail] = useState(false);

  const act = async (action: string, question?: string) => {
    if (question && !confirm(question)) return;
    setBusy(true);
    setError(null);
    try {
      if (action === 'duplicate') {
        const copy = await api<Campaign>(`/campaigns/${campaign.id}/duplicate`, 'POST');
        navigate(`/campaigns/${copy.id}`);
        return;
      }
      const updated = await api<Campaign>(`/campaigns/${campaign.id}/${action}`, 'POST');
      if (updated.status === 'draft') navigate(`/campaigns/${campaign.id}`);
      else reload();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  const done = stats.sent + stats.failed + stats.skipped;
  const percent = stats.total ? Math.round((done / stats.total) * 100) : 0;
  const tracked = stats.delivered + stats.bounced + stats.complained > 0;
  const rate = (n: number) => (stats.sent ? `${((n / stats.sent) * 100).toFixed(n && n / stats.sent < 0.01 ? 2 : 1)}%` : '—');

  return (
    <>
      <PageHeader
        title={campaign.name}
        intro={campaign.subject}
        actions={
          <>
            {campaign.status === 'sending' && <Button busy={busy} onClick={() => void act('pause')}>Pause</Button>}
            {campaign.status === 'paused' && <Button variant="primary" busy={busy} onClick={() => void act('resume')}>Resume</Button>}
            {campaign.status === 'scheduled' && (
              <Button busy={busy} onClick={() => void act('cancel', 'Cancel the schedule? The campaign goes back to being a draft you can edit.')}>
                Cancel and edit
              </Button>
            )}
            {(campaign.status === 'sending' || campaign.status === 'paused') && (
              <Button variant="danger" busy={busy}
                      onClick={() => void act('cancel', `Stop sending for good? ${count(stats.queued, 'person', 'people')} who haven't got it yet won't.`)}>
                Stop
              </Button>
            )}
            <Button busy={busy} onClick={() => void act('duplicate')}>Copy to a new draft</Button>
          </>
        }
      />
      {error && <Notice tone="error">{error}</Notice>}

      <div class="report__status">
        <span class={`badge badge--${campaign.status}`}>{STATUS_LABELS[campaign.status]}</span>
        <span class="muted">
          {campaign.status === 'scheduled' && <>Goes out {formatDate(campaign.scheduled_at)} to about {count(campaign.audience, 'person', 'people')}</>}
          {campaign.status === 'sending' && <>Started {formatDate(campaign.started_at)}</>}
          {campaign.status === 'paused' && <>Paused · {count(stats.queued, 'person', 'people')} still to go</>}
          {campaign.status === 'sent' && <>Sent {formatDate(campaign.sent_at)}</>}
          {campaign.status === 'cancelled' && <>Stopped {formatDate(campaign.updated_at)}</>}
        </span>
      </div>
      {campaign.last_error && campaign.status === 'paused' && (
        <Notice tone="error">Sending paused by itself: {campaign.last_error} Fix it in <Link href="/settings">Settings</Link>, then resume.</Notice>
      )}

      {stats.total > 0 && (
        <div class="progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label="Sent so far">
          <div class="progress__bar" style={{ width: `${percent}%` }} />
          <span>{done.toLocaleString()} of {stats.total.toLocaleString()} processed</span>
        </div>
      )}

      <div class="stats">
        <Stat value={stats.sent} label="sent" tone="good" />
        {stats.queued > 0 && <Stat value={stats.queued} label="waiting" />}
        {tracked && <Stat value={stats.delivered} label="delivered" />}
        <Stat value={stats.bounced} label={`bounced · ${rate(stats.bounced)}`} tone={stats.bounced / Math.max(stats.sent, 1) > 0.02 ? 'bad' : undefined} />
        <Stat value={stats.complained} label={`spam reports · ${rate(stats.complained)}`} tone={stats.complained / Math.max(stats.sent, 1) > 0.001 ? 'bad' : undefined} />
        <Stat value={stats.unsubscribed} label="unsubscribed" />
        {stats.failed > 0 && <Stat value={stats.failed} label="couldn't send" tone="bad" />}
        {stats.skipped > 0 && <Stat value={stats.skipped} label="skipped" />}
      </div>
      {stats.sent > 0 && !tracked && (
        <p class="hint">Deliveries, bounces and spam reports appear here once Amazon SES notifications are set up in <Link href="/settings#ses">Settings</Link>.</p>
      )}
      {stats.skipped > 0 && (
        <p class="hint">Skipped people unsubscribed or became unreachable after the campaign was scheduled, or it was stopped before reaching them.</p>
      )}

      <section class="card report__email">
        <div class="report__email-head">
          <h2 class="card__title">The email</h2>
          <Button onClick={() => setShowEmail((s) => !s)}>{showEmail ? 'Hide' : 'Show'}</Button>
        </div>
        {showEmail && <SentEmail id={campaign.id} />}
      </section>
    </>
  );
}

function SentEmail({ id }: { id: string }) {
  const [email] = useLoad(() => api<RenderedEmail>(`/campaigns/${id}/preview`, 'POST', {}), [id]);
  return (
    <Loading result={email}>
      {() => email.state === 'ready' && (
        <iframe class="report__frame" title="The email" sandbox="" srcDoc={email.data.html} />
      )}
    </Loading>
  );
}
