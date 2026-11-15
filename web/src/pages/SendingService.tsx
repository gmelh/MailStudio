/**
 * Choosing and connecting the service that delivers campaigns (Postmark, Mailgun,
 * SMTP2GO, AuthSMTP, any SMTP server, or Amazon SES), and setting up its bounce handling.
 */

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { api, type ConnectionCheck, type ProviderName, type SendingAccount, type Settings } from '../api';
import { Button, Checkbox, CopyText, describe, Field, Loading, Notice, Select, useLoad } from '../ui';

export const PROVIDERS: Array<{ key: ProviderName; name: string; blurb: string }> = [
  { key: 'postmark', name: 'Postmark', blurb: 'Excellent delivery and a quick setup. Uses a Broadcasts stream for newsletters.' },
  { key: 'mailgun', name: 'Mailgun', blurb: 'Popular and flexible, with US or EU data storage.' },
  { key: 'smtp2go', name: 'SMTP2GO', blurb: 'Fast signup and a simple dashboard.' },
  { key: 'authsmtp', name: 'AuthSMTP', blurb: 'An SMTP relay. MailStudio reads bounces from a mailbox you choose.' },
  { key: 'ses', name: 'Amazon SES', blurb: 'The cheapest at volume, with the most setup.' },
  { key: 'smtp', name: 'Other SMTP server', blurb: 'Any mail server that accepts SMTP logins.' },
];

export const providerName = (key: string | null | undefined): string =>
  PROVIDERS.find((p) => p.key === key)?.name ?? 'your sending service';

const SECURITY: Array<[string, string]> = [
  ['starttls', 'STARTTLS (usually port 587 or 2525)'],
  ['tls', 'TLS (usually port 465)'],
  ['none', 'None (local testing only)'],
];

/**
 * The chooser and the chosen service's form. `ses` renders the Amazon SES form (it lives
 * in Settings, with its own guide in Get set up).
 */
export function SendingServiceCard({ settings, onSaved, ses }: {
  settings: Settings;
  onSaved: () => void;
  ses: () => ComponentChildren;
}) {
  const [choice, setChoice] = useState<ProviderName | ''>(settings.provider.active);
  return (
    <section class="card form-card" id="sending-service">
      <h2>Sending service</h2>
      <p class="muted">The service that delivers your campaigns. Pick the one you have an account with, or the one that suits you best.
        You can change it later.</p>
      <div class="presets providers" role="radiogroup" aria-label="Sending service">
        {PROVIDERS.map((p) => (
          <button key={p.key} type="button" role="radio" aria-checked={choice === p.key}
                  class={`preset${choice === p.key ? ' is-active' : ''}`} onClick={() => setChoice(p.key)}>
            <strong>{p.name}{settings.provider.active === p.key && <span class="preset__current"> · in use</span>}</strong>
            <small>{p.blurb}</small>
          </button>
        ))}
      </div>
      {choice === 'ses' && ses()}
      {choice && choice !== 'ses' && <ProviderForm key={choice} provider={choice} settings={settings} onSaved={onSaved} />}
    </section>
  );
}

function ProviderForm({ provider, settings, onSaved }: { provider: Exclude<ProviderName, 'ses'>; settings: Settings; onSaved: () => void }) {
  const relay = settings.relay;
  const pm = settings.postmark;
  const mg = settings.mailgun;
  const authsmtp = provider === 'authsmtp';

  // Postmark
  const [pmToken, setPmToken] = useState('');
  const [pmStream, setPmStream] = useState(pm.message_stream || 'broadcast');
  const [pmOwn, setPmOwn] = useState(pm.own_unsubscribes);
  // Mailgun
  const [mgKey, setMgKey] = useState('');
  const [mgDomain, setMgDomain] = useState(mg.domain);
  const [mgRegion, setMgRegion] = useState<string>(mg.region);
  const [mgSigning, setMgSigning] = useState('');
  // SMTP2GO
  const [s2gKey, setS2gKey] = useState('');
  // SMTP relays
  const [host, setHost] = useState(relay.host || (authsmtp ? 'mail.authsmtp.com' : ''));
  const [port, setPort] = useState(String(relay.host ? relay.port : authsmtp ? 2525 : 587));
  const [username, setUsername] = useState(relay.username);
  const [password, setPassword] = useState('');
  const [security, setSecurity] = useState<string>(relay.security);
  const [bounce, setBounce] = useState(relay.bounce_address ?? '');
  const [imapHost, setImapHost] = useState(relay.imap_host);
  const [imapPort, setImapPort] = useState(String(relay.imap_port));
  const [imapUser, setImapUser] = useState(relay.imap_username);
  const [imapPassword, setImapPassword] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [check, setCheck] = useState<ConnectionCheck | null>(null);
  const keep = (value: string) => (value ? value.trim() : undefined); // empty: keep the saved secret

  const values = (): Record<string, unknown> => {
    switch (provider) {
      case 'postmark':
        return { postmark: { server_token: keep(pmToken), message_stream: pmStream.trim() || 'broadcast', own_unsubscribes: pmOwn } };
      case 'mailgun':
        return { mailgun: { api_key: keep(mgKey), domain: mgDomain.trim().toLowerCase(), region: mgRegion, webhook_signing_key: keep(mgSigning) } };
      case 'smtp2go':
        return { smtp2go: { api_key: keep(s2gKey) } };
      default:
        return {
          relay: {
            host: host.trim(), port: Number(port) || 587, username: username.trim(), password: password || undefined, security,
            bounce_address: bounce.trim() || null, imap_host: imapHost.trim(), imap_port: Number(imapPort) || 993,
            imap_username: imapUser.trim(), imap_password: imapPassword || undefined,
          },
        };
    }
  };

  const save = async (event: Event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCheck(null);
    try {
      await api('/settings', 'PATCH', { provider: { active: provider }, ...values() });
      setPmToken(''); setMgKey(''); setMgSigning(''); setS2gKey(''); setPassword(''); setImapPassword('');
      setCheck(await api<ConnectionCheck>('/sending/check', 'POST'));
      onSaved();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  const saved = (set: boolean) => (set ? 'Saved (type to replace)' : '');
  return (
    <form class="form" onSubmit={(e) => void save(e)}>
      <KeysGuide provider={provider} />
      {settings.provider.active && settings.provider.active !== provider && (
        <Notice>Saving switches your campaigns to {providerName(provider)} (from {providerName(settings.provider.active)}).</Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {check && <Notice tone={check.ok ? 'success' : 'error'}>{check.message}</Notice>}

      {provider === 'postmark' && (
        <>
          <div class="form-grid">
            <Field id="pm-token" label="Server API token" type="password" value={pmToken} onValue={setPmToken}
                   autoComplete="new-password" placeholder={saved(pm.server_token_set)} />
            <Field id="pm-stream" label="Broadcast stream ID" value={pmStream} onValue={setPmStream}
                   hint="Postmark's default is “broadcast”." />
          </div>
          <Checkbox label="Postmark lets me manage unsubscribes myself" checked={pmOwn} onChecked={setPmOwn}
                    hint="Postmark support switches this on for a stream when asked. Until then Postmark adds its own unsubscribe link
                          as well as MailStudio's, and its unsubscribes still reach MailStudio through the webhook below." />
        </>
      )}

      {provider === 'mailgun' && (
        <>
          <div class="form-grid">
            <Field id="mg-key" label="API key" type="password" value={mgKey} onValue={setMgKey}
                   autoComplete="new-password" placeholder={saved(mg.api_key_set)} />
            <Field id="mg-domain" label="Sending domain" value={mgDomain} onValue={setMgDomain} placeholder="mg.yourbakery.com.au"
                   hint="As it's listed in Mailgun." />
            <Select id="mg-region" label="Region" value={mgRegion} onValue={setMgRegion}
                    options={[['us', 'US (api.mailgun.net)'], ['eu', 'EU (api.eu.mailgun.net)']]} />
            <Field id="mg-signing" label="HTTP webhook signing key" type="password" value={mgSigning} onValue={setMgSigning}
                   autoComplete="new-password" placeholder={saved(mg.webhook_signing_key_set)}
                   hint="Lets MailStudio tell Mailgun's bounce reports from fakes." />
          </div>
        </>
      )}

      {provider === 'smtp2go' && (
        <Field id="s2g-key" label="API key" type="password" value={s2gKey} onValue={setS2gKey}
               autoComplete="new-password" placeholder={saved(settings.smtp2go.api_key_set)} />
      )}

      {(provider === 'authsmtp' || provider === 'smtp') && (
        <>
          <div class="form-grid">
            <Field id="relay-host" label="SMTP server" value={host} onValue={setHost} placeholder="smtp.example.com" />
            <Field id="relay-port" label="Port" type="number" value={port} onValue={setPort} />
            <Field id="relay-user" label="Username" value={username} onValue={setUsername} autoComplete="off" />
            <Field id="relay-password" label="Password" type="password" value={password} onValue={setPassword}
                   autoComplete="new-password" placeholder={saved(relay.password_set)} />
          </div>
          <Select id="relay-security" label="Security" value={security} onValue={setSecurity} options={SECURITY} />
          <h3 class="panel-title">Bounces</h3>
          <p class="muted small">SMTP can't report bounces directly, so they're sent to a bounce address. MailStudio reads that mailbox every
            five minutes and stops emailing addresses that bounce or report spam. Use a mailbox just for this: MailStudio marks the reports
            it handles as read and leaves anything else alone.</p>
          <Field id="relay-bounce" label="Bounce address" type="email" value={bounce} onValue={setBounce} placeholder="bounces@yourbakery.com.au"
                 hint={authsmtp ? 'Authorise this address in the AuthSMTP control panel, as you did your from address.' : undefined} />
          <div class="form-grid">
            <Field id="imap-host" label="Mailbox server (IMAP)" value={imapHost} onValue={setImapHost} placeholder="imap.yourmailhost.com" />
            <Field id="imap-port" label="IMAP port" type="number" value={imapPort} onValue={setImapPort} hint="993 for most servers." />
            <Field id="imap-user" label="Mailbox username" value={imapUser} onValue={setImapUser} autoComplete="off" />
            <Field id="imap-password" label="Mailbox password" type="password" value={imapPassword} onValue={setImapPassword}
                   autoComplete="new-password" placeholder={saved(relay.imap_password_set)} />
          </div>
        </>
      )}

      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save and check</Button></div>
    </form>
  );
}

function KeysGuide({ provider }: { provider: Exclude<ProviderName, 'ses'> }) {
  const steps: Record<typeof provider, ComponentChildren> = {
    postmark: (
      <ol>
        <li>In <a href="https://account.postmarkapp.com/" target="_blank" rel="noopener">Postmark</a>, create a server (or open yours).</li>
        <li>Newsletters must go through a <strong>Broadcasts</strong> message stream. If the server doesn't have one, add it under
          <strong> Message Streams</strong>. Its ID is usually <code>broadcast</code>.</li>
        <li>Open the server's <strong>API Tokens</strong> tab and copy the <strong>Server API token</strong>.</li>
        <li>Add your from address's domain under <strong>Sender Signatures</strong> and add the DNS records Postmark shows.</li>
      </ol>
    ),
    mailgun: (
      <ol>
        <li>In <a href="https://app.mailgun.com/" target="_blank" rel="noopener">Mailgun</a>, add your sending domain under
          <strong> Sending → Domains</strong> and add the DNS records it shows.</li>
        <li>Create an API key under <strong>API Security</strong>, and copy the <strong>HTTP webhook signing key</strong> from the same page.</li>
        <li>Choose EU below if your Mailgun account is in its EU region.</li>
      </ol>
    ),
    smtp2go: (
      <ol>
        <li>In <a href="https://app.smtp2go.com/" target="_blank" rel="noopener">SMTP2GO</a>, verify your sending domain under
          <strong> Sending → Verified Senders</strong>.</li>
        <li>Create a key under <strong>Sending → API Keys</strong>. Let it send email (MIME) and read stats.</li>
      </ol>
    ),
    authsmtp: (
      <ol>
        <li>Use the server, username and password from AuthSMTP's welcome email. Port 2525 with STARTTLS gets through most firewalls.</li>
        <li>In the <a href="https://control.authsmtp.com/" target="_blank" rel="noopener">AuthSMTP control panel</a>, authorise your
          from address and the bounce address below. AuthSMTP refuses mail from addresses it doesn't know.</li>
      </ol>
    ),
    smtp: <p>Use the SMTP details from your email provider. Ask them for the DKIM and SPF records for your domain too.</p>,
  };
  return (
    <details class="guide" open={provider !== 'smtp'}>
      <summary>Where to find these</summary>
      {steps[provider]}
    </details>
  );
}

/** Bounce and spam-report handling for every service but Amazon SES (which has its own card). */
export function BounceCard({ settings, onDone }: { settings: Settings; onDone?: () => void }) {
  const [account, reload] = useLoad(() => api<SendingAccount>('/sending/account'));
  const provider = settings.provider.active;
  const https = settings.base_url.startsWith('https://');
  return (
    <section class="card form-card" id="bounces">
      <h2>Bounces and spam reports</h2>
      <p class="muted">When an address bounces, someone marks your email as spam, or unsubscribes through {providerName(provider)}, MailStudio
        hears about it and never emails that address again. Keeping these low protects your ability to send.</p>
      {!provider && <Notice>Choose a sending service first.</Notice>}
      <Loading result={account}>
        {() => account.state === 'ready' && provider && (
          <BounceStatus provider={provider} account={account.data} https={https} />
        )}
      </Loading>
      {provider && (
        <div class="actions"><Button onClick={() => { reload(); onDone?.(); }}>Check again</Button></div>
      )}
    </section>
  );
}

const WEBHOOK_STEPS: Partial<Record<ProviderName, ComponentChildren>> = {
  postmark: (
    <ol>
      <li>In Postmark, open your server, then your Broadcasts stream, then <strong>Webhooks → Add webhook</strong>.</li>
      <li>Paste the address below. Tick <strong>Bounce</strong>, <strong>Spam complaint</strong> and <strong>Subscription change</strong>, and save.</li>
      <li>Use <strong>Send test</strong> on the webhook. This turns to done when MailStudio receives it.</li>
    </ol>
  ),
  mailgun: (
    <ol>
      <li>In Mailgun, open <strong>Sending → Webhooks</strong> and choose your sending domain.</li>
      <li>Add the address below for <strong>Permanent failure</strong>, <strong>Temporary failure</strong>, <strong>Spam complaints</strong> and
        <strong> Unsubscribes</strong>.</li>
      <li>Make sure the HTTP webhook signing key is saved above, then use <strong>Test webhook</strong>. This turns to done when MailStudio
        receives it.</li>
    </ol>
  ),
  smtp2go: (
    <ol>
      <li>In SMTP2GO, open <strong>Settings → Webhooks → Add webhook</strong>.</li>
      <li>Paste the address below, choose the <strong>Bounce</strong>, <strong>Spam</strong> and <strong>Unsubscribe</strong> events, and save.</li>
      <li>This turns to done when the first event arrives. Send yourself a test campaign, or use SMTP2GO's test option if it shows one.</li>
    </ol>
  ),
};

function BounceStatus({ provider, account, https }: { provider: ProviderName; account: SendingAccount; https: boolean }) {
  if (provider === 'ses') return null;
  if (account.webhook_url) {
    return (
      <div class="guide">
        {account.notifications === 'confirmed'
          ? <Notice tone="success">Working: {providerName(provider)} is sending MailStudio its reports.</Notice>
          : <Notice>Not set up yet. It takes a minute in {providerName(provider)}:</Notice>}
        {!https && <Notice tone="error">{providerName(provider)} can only reach MailStudio on a secure https:// address, which the installer sets up.</Notice>}
        {account.notifications === 'confirmed'
          ? (
            <details>
              <summary>How it's set up</summary>
              {WEBHOOK_STEPS[provider]}
              <p class="small"><CopyText text={account.webhook_url} /></p>
            </details>
          )
          : <>{WEBHOOK_STEPS[provider]}<p class="small"><CopyText text={account.webhook_url} /></p></>}
        <p class="muted small">This address is private to your MailStudio: anyone who has it can report bounces, so don't share it.</p>
      </div>
    );
  }
  const mailbox = account.mailbox;
  if (account.notifications === 'none') {
    return <Notice>Add a bounce address and its mailbox to the sending service above.</Notice>;
  }
  if (account.notifications === 'unknown') {
    return <Notice tone="error">Couldn't check the bounce mailbox: {mailbox?.error}</Notice>;
  }
  if (account.notifications === 'pending' || !mailbox?.checked_at) {
    return <Notice>The bounce mailbox will be checked within five minutes.</Notice>;
  }
  return (
    <Notice tone="success">
      Working: the bounce mailbox was checked {new Date(mailbox.checked_at).toLocaleString()}
      {mailbox.last_count ? ` and ${mailbox.last_count} report${mailbox.last_count === 1 ? '' : 's'} handled` : ''}.
    </Notice>
  );
}
