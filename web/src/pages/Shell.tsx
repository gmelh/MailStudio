/** The signed-in layout: navigation on the left, the current screen on the right. */

import type { JSX } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { api, type SetupStatus } from '../api';
import { Link, match, navigate, usePath } from '../router';
import { useSession } from '../session';
import { applyTheme, initialTheme, type Theme } from '../theme';
import { CampaignEditor } from '../editor/CampaignEditor';
import { Account } from './Account';
import { GettingStarted, setupChanged } from './GettingStarted';
import { CampaignReport } from './CampaignReport';
import { ContactDetail } from './ContactDetail';
import { Import } from './Import';
import { ListDetail } from './ListDetail';
import { SendCampaign } from './SendCampaign';
import { Suppressions } from './Suppressions';
import { Campaigns } from './Campaigns';
import { Contacts } from './Contacts';
import { Lists } from './Lists';
import { NotFound } from './NotFound';
import { SettingsPage } from './Settings';
import { Users } from './Users';

const NAV = [
  { href: '/campaigns', label: 'Campaigns' },
  { href: '/contacts', label: 'Contacts' },
  { href: '/lists', label: 'Lists' },
  { href: '/settings', label: 'Settings' },
  { href: '/users', label: 'Users' },
];

type Params = Record<string, string>;

/** Exact patterns, most specific first. */
const ROUTES: Array<[string, (p: Params) => JSX.Element]> = [
  ['/campaigns', () => <Campaigns />],
  ['/campaigns/:id/send', (p) => <SendCampaign key={p.id} id={p.id!} />],
  ['/campaigns/:id/report', (p) => <CampaignReport key={p.id} id={p.id!} />],
  ['/contacts', () => <Contacts />],
  ['/contacts/import', () => <Import />],
  ['/contacts/suppressed', () => <Suppressions />],
  ['/contacts/:id', (p) => <ContactDetail key={p.id} id={p.id!} />],
  ['/lists', () => <Lists />],
  ['/lists/:id', (p) => <ListDetail key={p.id} id={p.id!} />],
  ['/settings', () => <SettingsPage />],
  ['/users', () => <Users />],
  ['/account', () => <Account />],
  ['/setup', () => <GettingStarted />],
];

export function Brand() {
  return (
    <div class="brand">
      ✉ <span class="brand__accent">Mail</span> Studio
    </div>
  );
}

export function Shell() {
  const rawPath = usePath();
  const { session, signOut } = useSession();
  const [theme, setTheme] = useState<Theme>(initialTheme());

  // The home address shows campaigns; fix the address bar once listeners are in place.
  useEffect(() => {
    if (rawPath === '/') navigate('/campaigns', { replace: true });
  }, [rawPath]);
  const path = rawPath === '/' ? '/campaigns' : rawPath;

  // The editor takes the whole window.
  const editing = match('/campaigns/:id', path);
  let page: JSX.Element | null = null;
  for (const [pattern, render] of ROUTES) {
    const params = match(pattern, path);
    if (params) {
      page = render(params);
      break;
    }
  }

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setTheme(next);
  };

  if (editing) return <CampaignEditor key={editing.id} campaignId={editing.id!} />;

  return (
    <div class="shell">
      <aside class="sidebar">
        <Brand />
        <SetupNudge active={path === '/setup'} />
        <nav aria-label="Main">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              class={`nav-link${path.startsWith(item.href) ? ' is-active' : ''}`}
              aria-current={path.startsWith(item.href) ? 'page' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div class="sidebar__footer">
          <Link href="/account" class={`nav-link${path === '/account' ? ' is-active' : ''}`}>
            {session.user.name || session.user.email}
          </Link>
          <button type="button" class="nav-link nav-link--button" onClick={toggleTheme}>
            {theme === 'dark' ? '☀ Light mode' : '☾ Dark mode'}
          </button>
          <button type="button" class="nav-link nav-link--button" onClick={() => void signOut()}>
            Log out
          </button>
        </div>
      </aside>
      <main class="content">{page ?? <NotFound />}</main>
    </div>
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
    <Link href="/setup" class={`setup-nudge${active ? ' is-active' : ''}`}>
      <strong>Finish setting up</strong>
      <span>{done} of {status.steps.length} done</span>
    </Link>
  );
}
