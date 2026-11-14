/**
 * Top level: asks the server whether this install needs setting up and who is
 * logged in, then shows the setup screen, the login screen, or the app.
 */

import { useCallback, useEffect, useState } from 'preact/hooks';
import { api, onSessionEnded, setCsrfToken, type AuthStatus, type Session } from './api';
import { Login } from './pages/Login';
import { Setup } from './pages/Setup';
import { Shell } from './pages/Shell';
import { navigate } from './router';
import { SessionContext } from './session';
import { describe, Notice } from './ui';

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'setup' }
  | { kind: 'login' }
  | { kind: 'app'; session: Session };

export function App() {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const signedIn = useCallback((session: Session) => {
    setCsrfToken(session.csrf_token);
    setState({ kind: 'app', session });
  }, []);

  const signedOut = useCallback(() => {
    setCsrfToken(null);
    setState({ kind: 'login' });
  }, []);

  useEffect(() => {
    onSessionEnded(signedOut);
    api<AuthStatus>('/auth/status')
      .then((status) => {
        if (status.setup_required) setState({ kind: 'setup' });
        else if (status.user) signedIn(status.user);
        else setState({ kind: 'login' });
      })
      .catch((error: unknown) => setState({ kind: 'error', message: describe(error) }));
  }, [signedIn, signedOut]);

  switch (state.kind) {
    case 'loading':
      return null;
    case 'error':
      return (
        <main class="auth">
          <Notice tone="error">{state.message}</Notice>
        </main>
      );
    case 'setup':
      // Straight into the getting-started guide.
      return <Setup onDone={(session) => { navigate('/setup', { replace: true }); signedIn(session); }} />;
    case 'login':
      return <Login onDone={signedIn} />;
    case 'app': {
      const signOut = async () => {
        await api('/auth/logout', 'POST').catch(() => undefined);
        signedOut();
      };
      return (
        <SessionContext.Provider value={{ session: state.session, signOut, updateUser: signedIn }}>
          <Shell />
        </SessionContext.Provider>
      );
    }
  }
}
