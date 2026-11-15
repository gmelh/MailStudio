/** The getting-started guide: everything between a fresh install and the first campaign. */

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { api, type Settings, type SetupStatus, type SetupStep } from '../api';
import { setupChanged } from '../nav';
import { Link } from '../router';
import { Button, Loading, Notice, PageHeader, useLoad } from '../ui';
import { BounceCard, SendingServiceCard } from './SendingService';
import { BusinessCard, DomainCard, NotificationsCard, SenderCard, SesCard } from './Settings';

const TITLES: Record<SetupStep, [title: string, why: string]> = {
  business: ['Your business details', 'Your name and postal address go at the bottom of every email.'],
  sender: ['Who emails come from', 'The name and address people see in their inbox.'],
  provider: ['Choose how your emails are sent', 'Postmark, Mailgun, SMTP2GO, AuthSMTP or Amazon SES delivers them for you.'],
  domain: ['Prove your emails are really from you', 'A few DNS records, so inboxes trust you and spam folders don\'t.'],
  notifications: ['Handle bounces and spam reports', 'Stop emailing addresses that don\'t work or don\'t want you.'],
  production: ['Ask Amazon to let you send to anyone', 'New accounts can only email addresses they\'ve verified.'],
  audience: ['Bring in your subscribers', 'Import a spreadsheet, or start collecting signups.'],
};


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
      <PageHeader title="Get set up" intro="A few steps from a fresh install to your first campaign. Most take a few minutes." />
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
                <Notice>This MailStudio is at {setup.base_url}. For sending to real people, install it with the one-line installer, which gives it a secure https:// address.</Notice>
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
    case 'provider':
      return <SendingServiceCard settings={settings} onSaved={onChange} ses={() => <SesGuide settings={settings} onSaved={onChange} />} />;
    case 'domain':
      return setup.domain
        ? <DomainCard settings={settings} onChecked={(ready) => ready && onChange()} />
        : <Notice>Set the from address first (step 2): this step proves you own its domain.</Notice>;
    case 'notifications':
      return settings.provider.active === 'ses'
        ? <NotificationsCard settings={settings} onDone={onChange} />
        : <BounceCard settings={settings} onDone={onChange} />;
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
  return <SesCard settings={settings} onSaved={onSaved} embedded />;
}

function ProductionGuide({ onCheck }: { onCheck: () => void }) {
  return (
    <div class="card form-card guide">
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
