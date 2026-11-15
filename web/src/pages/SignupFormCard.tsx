/** A list's public signup form: settings, its link, and code to paste into a website. */

import { useState } from 'preact/hooks';
import { api, type MailingList, type SendingAccount, type Settings, type SignupForm } from '../api';
import { Link } from '../router';
import { Button, Checkbox, Field, Notice, TextArea, useLoad, useSubmit } from '../ui';

const escapeHtml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export function embedCode(action: string, form: SignupForm): string {
  const names = form.ask_name
    ? `
  <label>First name <input name="first_name" autocomplete="given-name"></label>
  <label>Last name <input name="last_name" autocomplete="family-name"></label>`
    : '';
  return `<form method="post" action="${escapeHtml(action)}">
  <label>Email <input type="email" name="email" required autocomplete="email"></label>${names}
  <!-- Leave this hidden field in: it stops spam bots. -->
  <div style="position:absolute;left:-10000px" aria-hidden="true"><input name="website" tabindex="-1" autocomplete="off"></div>
  <button type="submit">${escapeHtml(form.button || 'Subscribe')}</button>
</form>`;
}

export function SignupFormCard({ list, onSaved }: { list: MailingList; onSaved: () => void }) {
  const [context] = useLoad(() => Promise.all([api<Settings>('/settings'), api<SendingAccount>('/sending/account')]));
  const [form, setForm] = useState<SignupForm>(list.signup);
  const [doubleOptIn, setDoubleOptIn] = useState(list.double_opt_in);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const set = (change: Partial<SignupForm>) => { setSaved(false); setForm((f) => ({ ...f, ...change })); };

  const { busy, error, onSubmit } = useSubmit(async () => {
    await api(`/lists/${list.id}`, 'PATCH', { signup: form, double_opt_in: doubleOptIn });
    setSaved(true);
    onSaved();
  });

  const settings = context.state === 'ready' ? context.data[0] : null;
  const account = context.state === 'ready' ? context.data[1] : null;
  const pageUrl = settings ? `${settings.base_url}/f/${list.id}` : '';
  const live = list.signup.enabled;
  const code = embedCode(pageUrl, list.signup);

  return (
    <form class="card form-card signup-card" onSubmit={onSubmit} id="signup">
      <h2>Signup form</h2>
      <p class="muted">Let people join this list themselves, from a page MailStudio hosts or a form on your own website.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {saved && <Notice tone="success">Saved.</Notice>}

      <Checkbox label="Let people sign up with a form" checked={form.enabled} onChecked={(enabled) => set({ enabled })} />
      <Checkbox
        label="Ask new subscribers to confirm by email (recommended)"
        hint="They're only added once they click the link we email them. It keeps out typos and fake signups, and proves they agreed."
        checked={doubleOptIn}
        onChecked={(on) => { setSaved(false); setDoubleOptIn(on); }}
      />
      {doubleOptIn && form.enabled && settings && !settings.sender.from_email && (
        <Notice tone="error">Confirmation emails need a from address. Add one in <Link href="/settings#sender">Settings</Link>.</Notice>
      )}
      {doubleOptIn && form.enabled && account && !account.provider && (
        <Notice tone="error">Confirmation emails are sent with Amazon SES. <Link href="/settings#ses">Connect it in Settings</Link>.</Notice>
      )}

      {form.enabled && (
        <>
          <div class="form-grid">
            <Field label="Heading" value={form.title} onValue={(title) => set({ title })} placeholder={list.name} />
            <Field label="Button" value={form.button} onValue={(button) => set({ button })} />
          </div>
          <TextArea label="Introduction (optional)" value={form.intro} onValue={(intro) => set({ intro })} rows={2}
                    placeholder="News, recipes and the occasional offer, once a month." />
          <Checkbox label="Ask for their name" checked={form.ask_name} onChecked={(ask_name) => set({ ask_name })} />
          <TextArea label="Thank-you message (optional)" value={form.thanks} onValue={(thanks) => set({ thanks })} rows={2}
                    placeholder={doubleOptIn ? "Thanks! We've sent you an email: click the link in it to confirm." : "Thanks, you're subscribed."} />
          <Field label="Or go to this page after signing up (optional)" type="url" value={form.redirect_url}
                 onValue={(redirect_url) => set({ redirect_url })} placeholder="https://yourbakery.com.au/thanks" />
        </>
      )}
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save</Button></div>

      {live && settings && (
        <div class="signup-share">
          <h3 class="panel-title">Share it</h3>
          <p>Your signup page: <a href={pageUrl} target="_blank" rel="noopener">{pageUrl}</a></p>
          <p class="muted small">Link to it from your website, social media bio or email signature, or print it as a QR code.</p>
          <h3 class="panel-title">Or put the form on your website</h3>
          <p class="muted small">Paste this HTML where the form should appear. Style it to match your site.</p>
          <textarea class="code" readOnly rows={9} value={code} aria-label="Signup form HTML" onFocus={(e) => e.currentTarget.select()} />
          <div class="actions">
            <Button onClick={() => void navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
              {copied ? 'Copied' : 'Copy HTML'}
            </Button>
          </div>
        </div>
      )}
    </form>
  );
}
