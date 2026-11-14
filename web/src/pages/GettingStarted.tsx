/** The getting-started guide: everything between a fresh install and the first campaign. */

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { api, type Settings, type SetupStatus, type SetupStep } from '../api';
import { Link } from '../router';
import { Button, Loading, Notice, PageHeader, useLoad } from '../ui';
import { BusinessCard, DomainCard, NotificationsCard, SenderCard, SesCard } from './Settings';

const TITLES: Record<SetupStep, [title: string, why: string]> = {
  business: ['Your business details', 'Your name and postal address go at the bottom of every email.'],
  sender: ['Who emails come from', 'The name and address people see in their inbox.'],
  ses: ['Connect Amazon SES', 'The service that delivers your emails. About US$0.10 per thousand.'],
  domain: ['Prove your emails are really from you', 'A few DNS records, so inboxes trust you and spam folders don\'t.'],
  notifications: ['Handle bounces and spam reports', 'Stop emailing addresses that don\'t work or don\'t want you.'],
  production: ['Ask Amazon to let you send to anyone', 'New accounts can only email addresses they\'ve verified.'],
  audience: ['Bring in your subscribers', 'Import a spreadsheet, or start collecting signups.'],
};

/** Shared with the sidebar, so finishing a step updates its count. */
export const setupChanged = new EventTarget();

export function GettingStarted() {
  const [data, reload] = useLoad(() => Promise.all([api<SetupStatus>('/setup'), api<Settings>('/settings')]));
  const [open, setOpen] = useState<SetupStep | null>(null);
  const refresh = () => {
    reload();
    setupChanged.dispatchEvent(new Event('change'));
  };

  // Open the first unfinished step once the status arrives.
  const status = data.state === 'ready' ? data.data[0] : null;
  useEffect(() => {
    if (status && open === null) setOpen(status.steps.find((s) => !s.done)?.key ?? null);
  }, [status]);

  return (
    <>
      <PageHeader title="Get set up" intro="Seven steps from a fresh install to your first campaign. Most take a few minutes; Amazon's approval can take a day." />
      <Loading result={data}>
        {() => {
          if (data.state !== 'ready') return null;
          const [setup, settings] = data.data;
          const doneCount = setup.steps.filter((s) => s.done).length;
          return (
            <>
              <div class="setup-progress" role="progressbar" aria-valuenow={doneCount} aria-valuemin={0} aria-valuemax={setup.steps.length}>
                <div class="setup-progress__bar" style={{ width: `${(doneCount / setup.steps.length) * 100}%` }} />
              </div>
              <p class="muted setup-count">{doneCount} of {setup.steps.length} done</p>
              {!setup.https && (
                <Notice>This Mail Studio is at {setup.base_url}. For sending to real people, install it with the one-line installer, which gives it a secure https:// address.</Notice>
              )}
              {setup.complete && (
                <Notice tone="success">All set. <Link href="/campaigns">Write your first campaign →</Link></Notice>
              )}
              <ol class="steps">
                {setup.steps.map((step, index) => (
                  <Step key={step.key} number={index + 1} step={step.key} done={step.done}
                        open={open === step.key} onToggle={() => setOpen(open === step.key ? null : step.key)}>
                    <StepBody step={step.key} settings={settings} setup={setup} onChange={refresh} />
                  </Step>
                ))}
              </ol>
            </>
          );
        }}
      </Loading>
    </>
  );
}

function Step({ number, step, done, open, onToggle, children }: {
  number: number; step: SetupStep; done: boolean; open: boolean; onToggle: () => void; children: ComponentChildren;
}) {
  const [title, why] = TITLES[step];
  return (
    <li class={`step${done ? ' is-done' : ''}${open ? ' is-open' : ''}`}>
      <button type="button" class="step__head" aria-expanded={open} onClick={onToggle}>
        <span class="step__mark" aria-hidden="true">{done ? '✓' : number}</span>
        <span>
          <strong>{title}</strong>
          <small>{done ? 'Done' : why}</small>
        </span>
      </button>
      {open && <div class="step__body">{children}</div>}
    </li>
  );
}

function StepBody({ step, settings, setup, onChange }: {
  step: SetupStep; settings: Settings; setup: SetupStatus; onChange: () => void;
}) {
  switch (step) {
    case 'business':
      return <BusinessCard settings={settings} onSaved={onChange} />;
    case 'sender':
      return (
        <>
          <SenderCard settings={settings} onSaved={onChange} />
          <p class="hint">Use an address at your own domain (like news@yourbakery.com.au), not Gmail or Outlook: those can't be verified for sending.</p>
        </>
      );
    case 'ses':
      return <SesGuide settings={settings} onSaved={onChange} />;
    case 'domain':
      return setup.domain
        ? <DomainCard settings={settings} onChecked={(ready) => ready && onChange()} />
        : <Notice>Set the from address first (step 2): this step proves you own its domain.</Notice>;
    case 'notifications':
      return <NotificationsCard settings={settings} onDone={onChange} />;
    case 'production':
      return <ProductionGuide onCheck={onChange} />;
    case 'audience':
      return (
        <div class="card form-card">
          <p>Who will you email? Import people who've already agreed to hear from you, or turn on a signup form for your website.</p>
          <div class="actions">
            <Link href="/contacts/import" class="btn btn--primary">Import a spreadsheet</Link>
            <Link href="/lists" class="btn btn--plain">Set up a signup form</Link>
          </div>
          <p class="muted small">Only add people who asked to hear from you. Buying lists or adding people without asking gets accounts suspended.</p>
        </div>
      );
  }
}

function SesGuide({ settings, onSaved }: { settings: Settings; onSaved: () => void }) {
  const [policy] = useLoad(() => api<unknown>('/sending/iam-policy'));
  const [copied, setCopied] = useState(false);
  const text = policy.state === 'ready' ? JSON.stringify(policy.data, null, 2) : '';
  return (
    <>
      <div class="card form-card form-card--wide guide">
        <h2>Get your Amazon keys</h2>
        <ol>
          <li>Sign in to <a href="https://console.aws.amazon.com/" target="_blank" rel="noopener">the AWS console</a> (or create a free account).
            Pick the region nearest your subscribers at the top right, and note it.</li>
          <li>Open <a href="https://console.aws.amazon.com/iam/home#/users" target="_blank" rel="noopener">IAM → Users</a> and choose
            <strong> Create user</strong>. Name it <code>mailstudio</code>. Don't give it console access.</li>
          <li>On the permissions step, choose <strong>Attach policies directly → Create policy → JSON</strong>, paste the policy below,
            save it as <code>MailStudio</code>, then attach it to the user.</li>
          <li>Open the user, go to <strong>Security credentials → Create access key</strong>, choose
            <strong> Application running outside AWS</strong>, and copy both keys into the form below.</li>
        </ol>
        <textarea class="code" readOnly rows={8} value={text} aria-label="IAM policy" onFocus={(e) => e.currentTarget.select()} />
        <div class="actions">
          <Button onClick={() => void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
            {copied ? 'Copied' : 'Copy policy'}
          </Button>
        </div>
        <p class="muted small">These permissions let Mail Studio send email and set up its own notifications, and nothing else in your account.</p>
      </div>
      <SesCard settings={settings} onSaved={onSaved} />
    </>
  );
}

function ProductionGuide({ onCheck }: { onCheck: () => void }) {
  return (
    <div class="card form-card form-card--wide guide">
      <p>Every new Amazon SES account starts in a “sandbox”: it can only send to addresses you've verified. To email your subscribers, ask Amazon
        to lift that. It's a short form and is usually answered within a day.</p>
      <ol>
        <li>Open <a href="https://console.aws.amazon.com/ses/home#/account" target="_blank" rel="noopener">SES → Account dashboard</a> in the
          same region, and choose <strong>Request production access</strong>.</li>
        <li>Mail type: <strong>Marketing</strong>. Website: your business's website.</li>
        <li>Describe how you send, honestly. For example: “We email a monthly newsletter to customers who signed up on our website or in
          our shop. Every email has a one-click unsubscribe link. Bounces and complaints are handled automatically and those addresses are
          never emailed again. Signups from our website confirm their address by email first.”</li>
      </ol>
      <div class="actions"><Button onClick={onCheck}>Check again</Button></div>
    </div>
  );
}
