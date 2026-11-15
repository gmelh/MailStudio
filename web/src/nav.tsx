/**
 * The app bar across the top of every signed-in screen: the logo, the main sections, the
 * setup reminder, and your account. Everything is one click away from anywhere.
 */

import { useEffect, useState } from 'preact/hooks';
import { api, type SetupStatus } from './api';
import { Link, usePath } from './router';
import { useSession } from './session';
import { useTheme } from './theme';

export const NAV = [
  { href: '/campaigns', label: 'Campaigns' },
  { href: '/contacts', label: 'Contacts' },
  { href: '/lists', label: 'Lists' },
  { href: '/settings', label: 'Settings' },
  { href: '/users', label: 'Users' },
];

/** Fired when a setup step may have changed, so the reminder's count updates. */
export const setupChanged = new EventTarget();

/** The logo. As a link it goes to Campaigns, the app's home. */
export function Brand({ link = false }: { link?: boolean }) {
  const mark = (
    <>
      ✉ <span class="brand__accent">Mail</span>Studio
    </>
  );
  return link
    ? <Link href="/campaigns" class="brand brand--link">{mark}</Link>
    : <div class="brand">{mark}</div>;
}

export function AppBar() {
  const path = usePath();
  const { session, signOut } = useSession();
  const [theme, setTheme] = useTheme();
  const toggleTheme = () => setTheme(theme === 'dark' ? 'light' : 'dark');
  const current = (href: string) => path === href || path.startsWith(`${href}/`);

  return (
    <header class="app-bar">
      <Brand link />
      <nav aria-label="Main" class="app-bar__nav">
        {NAV.map((item) => (
          <Link key={item.href} href={item.href} class={`app-bar__link${current(item.href) ? ' is-active' : ''}`}
                aria-current={current(item.href) ? 'page' : undefined}>
            {item.label}
          </Link>
        ))}
      </nav>
      <SetupNudge active={path === '/setup'} />
      <div class="app-bar__user">
        <Link href="/account" class={`app-bar__link${path === '/account' ? ' is-active' : ''}`}
              aria-current={path === '/account' ? 'page' : undefined}>
          {session.user.name || session.user.email}
        </Link>
        <button type="button" class="app-bar__link app-bar__button" onClick={toggleTheme}
                aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
          {theme === 'dark' ? '☀' : '☾'}
        </button>
        <button type="button" class="app-bar__link app-bar__button" onClick={() => void signOut()}>
          Log out
        </button>
      </div>
    </header>
  );
}

/** "Finish setting up · 3 of 7" until everything's done. */
function SetupNudge({ active }: { active: boolean }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  useEffect(() => {
    const load = () => void api<SetupStatus>('/setup').then(setStatus).catch(() => undefined);
    load();
    setupChanged.addEventListener('change', load);
    return () => setupChanged.removeEventListener('change', load);
  }, []);
  if (!status || status.complete) return null;
  const done = status.steps.filter((s) => s.done).length;
  return (
    <Link href="/setup" class={`setup-nudge${active ? ' is-active' : ''}`} aria-current={active ? 'page' : undefined}>
      <strong>Finish setting up</strong>
      <span>{done} of {status.steps.length} done</span>
    </Link>
  );
}
