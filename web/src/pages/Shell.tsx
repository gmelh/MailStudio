/** The signed-in layout: the app bar across the top, the current screen below. */

import type { JSX } from 'preact';
import { useEffect } from 'preact/hooks';
import { match, navigate, usePath } from '../router';
import { CampaignEditor } from '../editor/CampaignEditor';
import { AppBar } from '../nav';
import { Account } from './Account';
import { GettingStarted } from './GettingStarted';
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

export function Shell() {
  const rawPath = usePath();

  // The home address shows campaigns; fix the address bar once listeners are in place.
  useEffect(() => {
    if (rawPath === '/') navigate('/campaigns', { replace: true });
  }, [rawPath]);
  const path = rawPath === '/' ? '/campaigns' : rawPath;

  // The editor has its own layout below the same app bar.
  const editing = match('/campaigns/:id', path);
  if (editing) return <CampaignEditor key={editing.id} campaignId={editing.id!} />;

  let page: JSX.Element | null = null;
  for (const [pattern, render] of ROUTES) {
    const params = match(pattern, path);
    if (params) {
      page = render(params);
      break;
    }
  }

  return (
    <div class="shell">
      <AppBar />
      <main class="content">{page ?? <NotFound />}</main>
    </div>
  );
}
