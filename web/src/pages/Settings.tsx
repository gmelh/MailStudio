/** Business details, sender, the sending service, sending domain, and the optional test-email server. */

import { useState } from 'preact/hooks';
import { api, type DomainCheck, type SendingAccount, type Settings } from '../api';
import { Button, Checkbox, CopyText, describe, Field, Loading, Notice, PageHeader, Select, TextArea, useLoad, useSubmit } from '../ui';
import { BounceCard, providerName, SendingServiceCard } from './SendingService';

const SES_REGIONS: Array<[string, string]> = [
  ['', 'Choose a region…'],
  ['ap-southeast-2', 'Asia Pacific (Sydney)'],
  ['ap-southeast-1', 'Asia Pacific (Singapore)'],
  ['ap-northeast-1', 'Asia Pacific (Tokyo)'],
  ['ap-south-1', 'Asia Pacific (Mumbai)'],
  ['us-east-1', 'US East (N. Virginia)'],
  ['us-east-2', 'US East (Ohio)'],
  ['us-west-2', 'US West (Oregon)'],
  ['ca-central-1', 'Canada (Central)'],
  ['eu-west-1', 'Europe (Ireland)'],
  ['eu-west-2', 'Europe (London)'],
  ['eu-central-1', 'Europe (Frankfurt)'],
  ['sa-east-1', 'South America (São Paulo)'],
];

export function SettingsPage() {
  const [settings, reload] = useLoad(() => api<Settings>('/settings'));
  return (
    <>
      <PageHeader title="Settings" intro="Your business details and how MailStudio sends email." />
      <Loading result={settings}>
        {() => settings.state === 'ready' && (
          <div class="settings">
            <BusinessCard settings={settings.data} />
            <SenderCard settings={settings.data} />
            <SendingServiceCard settings={settings.data} onSaved={reload}
                                ses={() => <SesCard settings={settings.data} onSaved={reload} embedded />} />
            {settings.data.provider.active === 'ses'
              ? <NotificationsCard settings={settings.data} />
              : <BounceCard settings={settings.data} />}
            <DomainCard settings={settings.data} />
            <SmtpCard settings={settings.data} />
            <section class="card form-card">
              <h2>Brand</h2>
              <p class="muted">Colours, fonts and logo are set in the email editor: open a campaign, choose “Brand & style”,
                then “Use as default brand”. New campaigns start with it.</p>
              <p class="muted small">This MailStudio's address: <code>{settings.data.base_url}</code></p>
            </section>
          </div>
        )}
      </Loading>
    </>
  );
}

function useSaved() {
  const [saved, setSaved] = useState(false);
  return { saved, mark: () => { setSaved(true); setTimeout(() => setSaved(false), 4000); }, clear: () => setSaved(false) };
}

export function BusinessCard({ settings, onSaved }: { settings: Settings; onSaved?: () => void }) {
  const [name, setName] = useState(settings.business.name);
  const [address, setAddress] = useState(settings.business.address);
  const done = useSaved();
  const { busy, error, onSubmit } = useSubmit(async () => {
    done.clear();
    await api('/settings', 'PATCH', { business: { name, address } });
    done.mark();
    onSaved?.();
  });
  return (
    <form class="card form-card" onSubmit={onSubmit} id="business">
      <h2>Your business</h2>
      <p class="muted">Shown at the bottom of every email. Anti-spam laws require a real postal address.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {done.saved && <Notice tone="success">Saved.</Notice>}
      <Field label="Business name" value={name} onValue={setName} />
      <TextArea label="Postal address" value={address} onValue={setAddress} rows={3} placeholder="12 High Street, Fremantle WA 6160" />
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save</Button></div>
    </form>
  );
}

export function SenderCard({ settings, onSaved }: { settings: Settings; onSaved?: () => void }) {
  const [fromName, setFromName] = useState(settings.sender.from_name);
  const [fromEmail, setFromEmail] = useState(settings.sender.from_email ?? '');
  const [replyTo, setReplyTo] = useState(settings.sender.reply_to ?? '');
  const [rate, setRate] = useState(String(settings.sending.rate_per_second));
  const done = useSaved();
  const { busy, error, onSubmit } = useSubmit(async () => {
    done.clear();
    await api('/settings', 'PATCH', {
      sender: { from_name: fromName, from_email: fromEmail || null, reply_to: replyTo || null },
      sending: { rate_per_second: Math.max(1, Math.min(200, Math.round(Number(rate)) || 10)) },
    });
    done.mark();
    onSaved?.();
  });
  return (
    <form class="card form-card" onSubmit={onSubmit} id="sender">
      <h2>Sender</h2>
      <p class="muted">Who new campaigns come from. You can change it for each campaign.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {done.saved && <Notice tone="success">Saved.</Notice>}
      <div class="form-grid">
        <Field label="From name" value={fromName} onValue={setFromName} placeholder="Fremantle Bakery" />
        <Field label="From address" type="email" value={fromEmail} onValue={setFromEmail} placeholder="news@yourbakery.com.au" />
      </div>
      <Field label="Replies go to (optional)" type="email" value={replyTo} onValue={setReplyTo} hint="Leave empty to receive replies at the from address." />
      <Field label="Emails per second" type="number" value={rate} onValue={setRate}
             hint="Keep this at or below your sending service's limit (Amazon SES starts new accounts at 14)." />
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save</Button></div>
    </form>
  );
}

export function SesCard({ settings, onSaved, embedded }: { settings: Settings; onSaved: () => void; embedded?: boolean }) {
  const ses = settings.ses;
  const [region, setRegion] = useState(ses.region);
  const [keyId, setKeyId] = useState(ses.access_key_id);
  const [secret, setSecret] = useState('');
  const [configSet, setConfigSet] = useState(ses.configuration_set);
  const [topic, setTopic] = useState(ses.notification_topic_arn);
  const [account, reloadAccount] = useLoad(() => api<SendingAccount>('/sending/account'));
  const done = useSaved();
  const { busy, error, onSubmit } = useSubmit(async () => {
    done.clear();
    await api('/settings', 'PATCH', {
      provider: { active: 'ses' },
      ses: {
        region, access_key_id: keyId.trim(), configuration_set: configSet.trim(), notification_topic_arn: topic.trim(),
        ...(secret ? { secret_access_key: secret.trim() } : {}),
      },
    });
    setSecret('');
    done.mark();
    reloadAccount();
    onSaved();
  });

  return (
    <form class={embedded ? 'form' : 'card form-card'} onSubmit={onSubmit} id="ses">
      {!embedded && <h2>Amazon SES</h2>}
      <p class="muted">Campaigns are sent through Amazon Simple Email Service: reliable, and about US$0.10 per thousand emails.
        Create an access key for an IAM user with SES permissions, then paste it here.</p>
      <SesKeysGuide open={!(ses.region && ses.access_key_id && ses.secret_set)} />
      <Loading result={account}>
        {() => account.state === 'ready' && (account.data.provider === 'ses' || !embedded
          ? <AccountStatus account={account.data} />
          : <Notice>Saving switches your campaigns to Amazon SES{account.data.label ? ` (from ${account.data.label})` : ''}.</Notice>)}
      </Loading>
      {error && <Notice tone="error">{error}</Notice>}
      {done.saved && <Notice tone="success">Saved.</Notice>}
      <Select label="Region" value={region} onValue={setRegion} options={SES_REGIONS.some(([v]) => v === region) ? SES_REGIONS : [...SES_REGIONS, [region, region]]} />
      <div class="form-grid">
        <Field label="Access key ID" value={keyId} onValue={setKeyId} autoComplete="off" />
        <Field label="Secret access key" type="password" value={secret} onValue={setSecret} autoComplete="new-password"
               placeholder={ses.secret_set ? 'Saved (type to replace)' : ''} />
      </div>
      <details>
        <summary>Advanced: notifications set up by hand</summary>
        <div class="form">
          <p class="muted small">“Bounces and spam reports” below sets these up for you. To do it by hand instead: have SES publish
            events to an SNS topic, subscribe <code>{settings.base_url}/webhooks/ses</code> to it (HTTPS), and enter the topic here.</p>
          <Field label="SNS topic ARN" value={topic} onValue={setTopic} placeholder="arn:aws:sns:ap-southeast-2:123456789012:ses-notifications" />
          <Field label="Configuration set (optional)" value={configSet} onValue={setConfigSet} />
        </div>
      </details>
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save and check</Button></div>
    </form>
  );
}

/** Where Amazon SES keys come from: IAM, not the SES console. */
function SesKeysGuide({ open }: { open: boolean }) {
  const [policy] = useLoad(() => api<unknown>('/sending/iam-policy'));
  const [copied, setCopied] = useState(false);
  const text = policy.state === 'ready' ? JSON.stringify(policy.data, null, 2) : '';
  return (
    <details class="guide" open={open}>
      <summary>Where to find these</summary>
      <p>The keys come from <strong>IAM</strong> (AWS's users and permissions), not from the SES pages.</p>
      <ol>
        <li>Sign in to <a href="https://console.aws.amazon.com/" target="_blank" rel="noopener">the AWS console</a> (or create a free account).
          Pick the region nearest your subscribers at the top right, and choose the same one below.</li>
        <li>Open <a href="https://console.aws.amazon.com/iam/home#/users" target="_blank" rel="noopener">IAM → Users</a> and choose
          <strong> Create user</strong>. Name it <code>mailstudio</code>. Don't give it console access.</li>
        <li>On the permissions step, choose <strong>Attach policies directly → Create policy → JSON</strong>, paste the policy below,
          save it as <code>MailStudio</code>, then attach it to the user.</li>
        <li>Open the user, go to <strong>Security credentials → Create access key</strong>, choose
          <strong> Application running outside AWS</strong>, and copy both parts into the form below. AWS shows the secret only once:
          if it's lost, create a new key and deactivate the old one.</li>
      </ol>
      <textarea class="code" readOnly rows={8} value={text} aria-label="IAM policy" onFocus={(e) => e.currentTarget.select()} />
      <div class="actions">
        <Button onClick={() => void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
          {copied ? 'Copied' : 'Copy policy'}
        </Button>
      </div>
      <p class="muted small">These permissions let MailStudio send email and set up its own notifications, and nothing else in your account.</p>
    </details>
  );
}

function AccountStatus({ account }: { account: SendingAccount }) {
  if (!account.provider) return <Notice tone="error">Not connected yet. Campaigns can't be sent until it is.</Notice>;
  return (
    <div class="status-box">
      {account.ses ? (
        <p>
          <strong>Connected</strong> ({account.ses.region}) ·{' '}
          {account.ses.production_access ? 'Production access' : 'Sandbox (only verified addresses)'} ·{' '}
          {account.ses.sent_last_24_hours.toLocaleString()} of {account.ses.max_24_hour_send.toLocaleString()} sent in the last 24 hours ·{' '}
          up to {account.ses.max_send_rate}/s
        </p>
      ) : (
        <p><strong>Sending with:</strong> {account.label ?? account.provider}</p>
      )}
      {account.warnings.map((w) => <Notice key={w}>{w}</Notice>)}
    </div>
  );
}

const RECORD_STATUS: Record<string, string> = { ok: '✓ Found', missing: '✗ Missing', wrong: '✗ Wrong value', warning: '! Check' };

export function DomainCard({ settings, onChecked }: { settings: Settings; onChecked?: (ready: boolean) => void }) {
  const fromDomain = settings.sender.from_email?.split('@')[1] ?? '';
  const [domain, setDomain] = useState(fromDomain);
  const [check, setCheck] = useState<DomainCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const active = settings.provider.active;
  const usesSes = active === 'ses';
  const connected = usesSes ? Boolean(settings.ses.region && settings.ses.access_key_id && settings.ses.secret_set) : Boolean(active);

  const run = async (register: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const name = domain.trim().toLowerCase();
      const result = register
        ? await api<DomainCheck>('/sending/domain', 'POST', { domain: name })
        : await api<DomainCheck>(`/sending/domain/${encodeURIComponent(name)}`);
      setCheck(result);
      onChecked?.(result.ready);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section class="card form-card" id="domain">
      <h2>Sending domain</h2>
      <p class="muted">Prove to inboxes that your emails really come from you, so they don't land in spam.
        You add a few records at the company that manages your domain name (where you bought it).</p>
      {!connected && <Notice>{usesSes ? 'Connect Amazon SES above first.' : 'Choose a sending service above first.'}</Notice>}
      {connected && !usesSes && (
        <Notice>{providerName(active)} checks the signing (DKIM) records itself. {check?.provider_note ?? ''} MailStudio checks DMARC,
          which Gmail and Yahoo require, and warns about SPF problems.</Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <form class="toolbar" onSubmit={(e) => { e.preventDefault(); void run(usesSes && !check?.registered_with_ses); }}>
        <Field label="Domain" value={domain} onValue={setDomain} placeholder="yourbakery.com.au" />
        <Button type="submit" variant="primary" busy={busy} disabled={!connected || !domain.trim()}>
          {!usesSes ? (check ? 'Check again' : 'Check') : check?.registered_with_ses ? 'Check again' : 'Set up'}
        </Button>
        {usesSes && !check && <Button busy={busy} disabled={!connected || !domain.trim()} onClick={() => void run(false)}>Check</Button>}
      </form>
      {check && (
        <>
          {check.ready
            ? <Notice tone="success">{check.domain} is ready. Emails from it will be signed and trusted.</Notice>
            : <Notice>Add the records below{usesSes ? '' : ` (and the ones ${providerName(active)} shows you)`}, then choose “Check again”.
                Changes can take from a few minutes to a few hours to show up.</Notice>}
          <div class="table-scroll">
            <table class="table table--plain dns">
              <thead><tr><th>Type</th><th>Name (host)</th><th>Value</th><th>Status</th></tr></thead>
              <tbody>
                {check.records.map((r) => (
                  <tr key={r.host + r.value}>
                    <td>{r.type}<div class="muted small">{r.purpose}</div></td>
                    <td><CopyText text={r.host} /></td>
                    <td><CopyText text={r.value} />{r.status !== 'ok' && r.help && <div class="muted small">{r.help}</div>}</td>
                    <td class={`dns__status dns__status--${r.status}`}>{RECORD_STATUS[r.status] ?? r.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function SmtpCard({ settings }: { settings: Settings }) {
  const smtp = settings.smtp;
  const [enabled, setEnabled] = useState(Boolean(smtp.host));
  const [host, setHost] = useState(smtp.host);
  const [port, setPort] = useState(String(smtp.port));
  const [username, setUsername] = useState(smtp.username);
  const [password, setPassword] = useState('');
  const [security, setSecurity] = useState<string>(smtp.security);
  const done = useSaved();
  const { busy, error, onSubmit } = useSubmit(async () => {
    done.clear();
    await api('/settings', 'PATCH', {
      smtp: enabled
        ? { host: host.trim(), port: Number(port) || 587, username, security, ...(password ? { password } : {}) }
        : { host: '', port: 587, username: '', password: '', security: 'starttls' },
    });
    setPassword('');
    done.mark();
  });
  return (
    <form class="card form-card" onSubmit={onSubmit} id="smtp">
      <h2>Test emails</h2>
      <p class="muted">Test emails go through your sending service. If you'd rather send them through another mail server, add it here.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {done.saved && <Notice tone="success">Saved.</Notice>}
      <Checkbox label="Send test emails through my own mail server (SMTP)" checked={enabled} onChecked={setEnabled} />
      {enabled && (
        <>
          <div class="form-grid">
            <Field label="Server" value={host} onValue={setHost} placeholder="smtp.example.com" />
            <Field label="Port" type="number" value={port} onValue={setPort} />
            <Field label="Username" value={username} onValue={setUsername} autoComplete="off" />
            <Field label="Password" type="password" value={password} onValue={setPassword} autoComplete="new-password"
                   placeholder={smtp.password_set ? 'Saved (type to replace)' : ''} />
          </div>
          <Select label="Security" value={security} onValue={setSecurity}
                  options={[['starttls', 'STARTTLS (usually port 587)'], ['tls', 'TLS (usually port 465)'], ['none', 'None (local testing only)']]} />
        </>
      )}
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save</Button></div>
    </form>
  );
}

export function NotificationsCard({ settings, onDone }: { settings: Settings; onDone?: () => void }) {
  const [account, reload] = useLoad(() => api<SendingAccount>('/sending/account'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const https = settings.base_url.startsWith('https://');
  const connected = Boolean(settings.ses.region && settings.ses.access_key_id && settings.ses.secret_set);
  const status = account.state === 'ready' ? account.data.notifications : 'none';

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await api('/sending/notifications', 'POST');
      // Amazon confirms the connection within a few seconds.
      await new Promise((resolve) => setTimeout(resolve, 4000));
      reload();
      onDone?.();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section class="card form-card" id="notifications">
      <h2>Bounces and spam reports</h2>
      <p class="muted">When an address bounces or someone marks your email as spam, Amazon tells MailStudio, and that address is
        never emailed again. Keeping these low protects your ability to send.</p>
      {!connected && <Notice>Connect Amazon SES first.</Notice>}
      {connected && !https && (
        <Notice tone="error">This needs MailStudio on a secure https:// address, which the installer sets up. This one is {settings.base_url}.</Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {status === 'confirmed' && <Notice tone="success">Set up and working.</Notice>}
      {status === 'pending' && <Notice>Waiting for Amazon to confirm the connection. Check again in a minute.</Notice>}
      {status === 'unknown' && <Notice>Couldn't check with Amazon just now.</Notice>}
      <div class="actions">
        {status !== 'confirmed' && (
          <Button variant="primary" busy={busy} disabled={!connected || !https} onClick={() => void run()}>Set it up for me</Button>
        )}
        {status !== 'none' && <Button onClick={reload}>Check again</Button>}
      </div>
      <p class="muted small">This creates a notification topic and a configuration set named “mailstudio” in your AWS account.</p>
    </section>
  );
}
