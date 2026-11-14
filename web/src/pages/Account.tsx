import { useState } from 'preact/hooks';
import { api } from '../api';
import { useSession } from '../session';
import { Button, Field, Notice, PageHeader, useSubmit } from '../ui';

export function Account() {
  const { session } = useSession();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [done, setDone] = useState(false);
  const { busy, error, onSubmit } = useSubmit(async () => {
    setDone(false);
    await api('/auth/password', 'POST', { current_password: current, new_password: next });
    setCurrent('');
    setNext('');
    setDone(true);
  });

  return (
    <>
      <PageHeader title="Your account" intro={`Logged in as ${session.user.email}.`} />
      <form class="card form-card" onSubmit={onSubmit}>
        <h2>Change password</h2>
        {error && <Notice tone="error">{error}</Notice>}
        {done && <Notice tone="success">Password changed. You've been logged out on your other devices.</Notice>}
        <Field
          label="Current password"
          type="password"
          value={current}
          onValue={setCurrent}
          autoComplete="current-password"
          required
        />
        <Field
          label="New password"
          type="password"
          value={next}
          onValue={setNext}
          autoComplete="new-password"
          hint="At least 10 characters."
          required
        />
        <div class="actions">
          <Button type="submit" variant="primary" busy={busy}>Change password</Button>
        </div>
      </form>
    </>
  );
}
