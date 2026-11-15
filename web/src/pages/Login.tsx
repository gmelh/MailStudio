import { useState } from 'preact/hooks';
import { api, type Session } from '../api';
import { Brand } from '../nav';
import { Button, Field, Notice, useSubmit } from '../ui';

export function Login({ onDone }: { onDone: (session: Session) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const { busy, error, onSubmit } = useSubmit(async () => {
    onDone(await api<Session>('/auth/login', 'POST', { email, password }));
  });

  return (
    <main class="auth">
      <form class="card auth__card" onSubmit={onSubmit}>
        <Brand />
        <h1>Log in</h1>
        {error && <Notice tone="error">{error}</Notice>}
        <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="username" required />
        <Field
          label="Password"
          type="password"
          value={password}
          onValue={setPassword}
          autoComplete="current-password"
          required
        />
        <Button type="submit" variant="primary" busy={busy}>
          Log in
        </Button>
        <p class="muted small">
          Locked out? Someone with access to the server can run <code>mailstudio reset-password --email you@…</code>
        </p>
      </form>
    </main>
  );
}
