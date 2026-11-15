/**
 * Getting a draft out the door: who it goes to, who it's from, a test, a final
 * check of everything the server will insist on, then now or later.
 */

import { useEffect, useState } from 'preact/hooks';
import { all, api, ApiError, type Campaign, type MailingList, type SendingAccount, type Settings } from '../api';
import { Link, navigate } from '../router';
import { useSession } from '../session';
import { Button, Checkbox, count, describe, Field, Loading, Notice, PageHeader, useLoad, UserError, useSubmit } from '../ui';

export function SendCampaign({ id }: { id: string }) {
  const [data] = useLoad(
    () => Promise.all([
      api<Campaign>(`/campaigns/${id}`),
      all<MailingList>('/lists'),
      api<Settings>('/settings'),
      api<SendingAccount>('/sending/account'),
      api<ImageChecks>(`/campaigns/${id}/checks`),
    ]),
    [id],
  );
  return (
    <>
      <p class="crumbs"><Link href={`/campaigns/${id}`}>← Back to the editor</Link></p>
      <Loading result={data}>
        {() => {
          if (data.state !== 'ready') return null;
          const [campaign, lists, settings, account, images] = data.data;
          if (campaign.status !== 'draft') return <Redirect to={`/campaigns/${id}/report`} />;
          return <SendForm initial={campaign} lists={lists} settings={settings} account={account} images={images} />;
        }}
      </Loading>
    </>
  );
}

function Redirect({ to }: { to: string }) {
  useEffect(() => navigate(to, { replace: true }), [to]);
  return null;
}

/** From GET /campaigns/{id}/checks: problems stop sending; warnings are worth fixing. */
interface ImageChecks {
  problems: string[];
  warnings: string[];
}

function SendForm({ initial, lists, settings, account, images }: {
  initial: Campaign; lists: MailingList[]; settings: Settings; account: SendingAccount; images: ImageChecks;
}) {
  const { session } = useSession();
  const [campaign, setCampaign] = useState(initial);
  const [chosen, setChosen] = useState<string[]>(initial.lists.length ? initial.lists : lists.length === 1 ? [lists[0]!.id] : []);
  const [tags, setTags] = useState(initial.tags.join(', '));
  const [fromName, setFromName] = useState(initial.from_name);
  const [fromEmail, setFromEmail] = useState(initial.from_email);
  const [replyTo, setReplyTo] = useState(initial.reply_to);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [when, setWhen] = useState<'now' | 'later'>('now');
  const [at, setAt] = useState(defaultLater());
  const [confirming, setConfirming] = useState(false);

  // Save audience and sender as they change; the server counts the audience.
  useEffect(() => {
    const timer = setTimeout(() => {
      const tagList = tags.split(',').map((t) => t.trim()).filter(Boolean);
      api<Campaign>(`/campaigns/${campaign.id}`, 'PATCH', {
        lists: chosen, tags: tagList, from_name: fromName,
        ...(fromEmail.trim() ? { from_email: fromEmail.trim() } : {}),
        ...(replyTo.trim() ? { reply_to: replyTo.trim() } : {}),
      })
        .then((c) => { setCampaign(c); setSaveError(null); })
        .catch((e: unknown) => setSaveError(describe(e)));
    }, 400);
    return () => clearTimeout(timer);
  }, [chosen, tags, fromName, fromEmail, replyTo]);

  const checks: Array<[ok: boolean, text: string, fix?: string]> = [
    [Boolean(settings.business.name && settings.business.address), 'Business name and postal address are set', '/settings#business'],
    [Boolean(campaign.subject.trim()), 'The email has a subject line', `/campaigns/${campaign.id}`],
    [Boolean(campaign.from_email), 'It has a from address'],
    [campaign.lists.length > 0, 'It goes to at least one list'],
    [campaign.audience > 0, campaign.lists.length ? `${count(campaign.audience, 'person', 'people')} will get it` : 'Somebody will get it'],
    [!images.problems.length, 'Every image can be seen by recipients', `/campaigns/${campaign.id}`],
    [Boolean(account.provider), account.label ? `Sending through ${account.label}` : account.provider ? `Sending through ${account.provider}` : 'A sending service is chosen', '/settings#sending-service'],
  ];
  const ready = checks.every(([ok]) => ok) && !saveError;

  const send = useSubmit(async () => {
    let body: { at?: string } | undefined;
    if (when === 'later') {
      const moment = new Date(at);
      if (Number.isNaN(moment.getTime()) || moment.getTime() < Date.now()) throw new UserError('Choose a time in the future.');
      body = { at: moment.toISOString() };
    }
    try {
      await api(`/campaigns/${campaign.id}/schedule`, 'POST', body);
    } catch (e) {
      setConfirming(false);
      throw e;
    }
    navigate(`/campaigns/${campaign.id}/report`);
  });

  return (
    <>
      <PageHeader title={`Send “${campaign.name}”`} intro={campaign.subject || 'No subject yet'} />
      {saveError && <Notice tone="error">{saveError}</Notice>}

      <div class="send">
        <div class="send__main">
          <section class="card form-card">
            <h2>Who gets it</h2>
            {!lists.length && <p class="muted">You don't have any lists yet. <Link href="/lists">Create one</Link> and import your contacts.</p>}
            {lists.map((l) => (
              <Checkbox key={l.id} checked={chosen.includes(l.id)}
                        label={<><strong>{l.name}</strong> <span class="muted">· {count(l.counts.subscribed, 'person', 'people')}</span></>}
                        onChecked={(on) => setChosen((c) => (on ? [...c, l.id] : c.filter((x) => x !== l.id)))} />
            ))}
            <Field label="Only people tagged (optional)" value={tags} onValue={setTags}
                   hint="Comma-separated. Leave empty to send to everyone on the lists." />
            <p class="audience" aria-live="polite">
              {campaign.lists.length ? <>Goes to <strong>{count(campaign.audience, 'person', 'people')}</strong>.</> : 'Choose a list.'}
              <span class="muted small"> People who unsubscribed, bounced or complained are always left out.</span>
            </p>
          </section>

          <section class="card form-card">
            <h2>Who it's from</h2>
            <div class="form-grid">
              <Field label="From name" value={fromName} onValue={setFromName} />
              <Field label="From address" type="email" value={fromEmail} onValue={setFromEmail} />
            </div>
            <Field label="Replies go to (optional)" type="email" value={replyTo} onValue={setReplyTo} />
          </section>

          <TestSend campaign={campaign} defaultTo={session.user.email} />
        </div>

        <aside class="send__side">
          <section class="card form-card">
            <h2>Ready to send?</h2>
            <ul class="checklist">
              {checks.map(([ok, text, fix]) => (
                <li key={text} class={ok ? 'is-ok' : 'is-missing'}>
                  <span aria-hidden="true">{ok ? '✓' : '○'}</span>
                  <span>{text}{!ok && fix && <> · <Link href={fix}>fix</Link></>}</span>
                </li>
              ))}
            </ul>
            {images.problems.map((p) => <Notice key={p} tone="error">{p}</Notice>)}
            {images.warnings.length > 0 && (
              <details class="image-advice" open={images.warnings.length <= 3}>
                <summary>{count(images.warnings.length, 'suggestion', 'suggestions')} for your images</summary>
                <ul>{images.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                <p class="muted small"><Link href={`/campaigns/${campaign.id}`}>Fix in the editor</Link>, or send as it is.</p>
              </details>
            )}
            {account.warnings.map((w) => <Notice key={w}>{w}</Notice>)}

            <fieldset class="consent">
              <legend>When</legend>
              <label class="check"><input type="radio" name="when" checked={when === 'now'} onChange={() => setWhen('now')} /><span>Now</span></label>
              <label class="check"><input type="radio" name="when" checked={when === 'later'} onChange={() => setWhen('later')} /><span>Later</span></label>
              {when === 'later' && (
                <input type="datetime-local" aria-label="Send at" value={at} min={defaultLater(0)} onInput={(e) => setAt(e.currentTarget.value)} />
              )}
            </fieldset>

            {send.error && <Notice tone="error">{send.error}</Notice>}
            {!confirming ? (
              <Button variant="primary" disabled={!ready} onClick={() => setConfirming(true)}>
                {when === 'now' ? `Send to ${count(campaign.audience, 'person', 'people')}` : 'Schedule'}
              </Button>
            ) : (
              <form class="inset" onSubmit={send.onSubmit}>
                <p>
                  {when === 'now'
                    ? <>Send now to <strong>{count(campaign.audience, 'person', 'people')}</strong>? You can pause it while it's going out.</>
                    : <>Send at <strong>{new Date(at).toLocaleString()}</strong>? You can cancel before then and keep editing.</>}
                </p>
                <div class="actions">
                  <Button type="submit" variant="primary" busy={send.busy}>{when === 'now' ? 'Yes, send it' : 'Yes, schedule it'}</Button>
                  <Button onClick={() => setConfirming(false)}>Not yet</Button>
                </div>
              </form>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}

function TestSend({ campaign, defaultTo }: { campaign: Campaign; defaultTo: string }) {
  const [to, setTo] = useState(defaultTo);
  const [sent, setSent] = useState<string | null>(null);
  const { busy, error, onSubmit } = useSubmit(async () => {
    setSent(null);
    const addresses = to.split(/[,\s]+/).filter(Boolean);
    if (!addresses.length) throw new UserError('Enter an address to send the test to.');
    try {
      await api(`/campaigns/${campaign.id}/test`, 'POST', { to: addresses.slice(0, 5) });
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.type.endsWith('no-sender')) throw new UserError('Add a from address first.');
      throw e;
    }
    setSent(addresses.slice(0, 5).join(', '));
  });
  return (
    <form class="card form-card" onSubmit={onSubmit}>
      <h2>Send yourself a test</h2>
      <p class="muted">See it in a real inbox before anyone else does. Subscribers aren't emailed.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {sent && <Notice tone="success">Test sent to {sent}. It can take a minute to arrive.</Notice>}
      <div class="toolbar">
        <Field label="Send a test to" value={to} onValue={setTo} hint="Up to 5 addresses, separated by commas." />
        <Button type="submit" busy={busy}>Send test</Button>
      </div>
    </form>
  );
}

/** A datetime-local value `hours` ahead, rounded to the next quarter hour. */
function defaultLater(hours = 1): string {
  const d = new Date(Date.now() + hours * 3600_000);
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
