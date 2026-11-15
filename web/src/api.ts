/**
 * Talking to the MailStudio API from the browser.
 *
 * The session cookie travels automatically. Every change also carries the
 * session's CSRF token, which the server checks so other websites can't act
 * on the user's behalf. Errors arrive as RFC 9457 problem details and are
 * thrown as ApiError.
 */

export interface FieldError {
  location: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly detail: string | undefined;
  readonly errors: FieldError[];

  constructor(status: number, body: Record<string, unknown> | null) {
    super(String(body?.title ?? `Request failed (${status})`));
    this.status = status;
    this.type = String(body?.type ?? '');
    this.detail = typeof body?.detail === 'string' ? body.detail : undefined;
    this.errors = Array.isArray(body?.errors) ? (body.errors as FieldError[]) : [];
  }

  /** One line suitable for showing to a person. */
  get summary(): string {
    if (this.errors.length) return this.errors.map((e) => e.message).join(' ');
    return this.detail ? `${this.message}. ${this.detail}` : this.message;
  }
}

let csrfToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Called when the server says the session has ended (expired, or logged out elsewhere). */
export function onSessionEnded(handler: () => void): void {
  onSignedOut = handler;
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';

export async function api<T>(path: string, method: Method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;

  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;

  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const problem = (data && typeof data === 'object' ? data : null) as Record<string, unknown> | null;
    if (response.status === 401 && !path.startsWith('/auth/')) onSignedOut?.();
    throw new ApiError(response.status, problem);
  }
  return data as T;
}

// ---------------------------------------------------------------------------
// Shapes returned by the API (the parts the app uses)

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: string;
  last_login_at: string | null;
}

export interface Session {
  user: User;
  csrf_token: string;
}

export interface AuthStatus {
  setup_required: boolean;
  user: Session | null;
}

export interface Page<T> {
  data: T[];
  next_cursor: string | null;
}

export interface CampaignSummary {
  id: string;
  name: string;
  subject: string;
  status: 'draft' | 'scheduled' | 'sending' | 'paused' | 'sent' | 'cancelled';
  audience: number;
  scheduled_at: string | null;
  started_at: string | null;
  sent_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Palette {
  outer: string;
  background: string;
  featured: string;
  text: string;
  heading: string;
  link: string;
  accent: string;
  buttonText: string;
}

export interface Brand {
  width: number;
  padding: number;
  radius: number;
  fontSize: number;
  fonts: { heading: string; body: string; small: string };
  button: { bold: boolean; italic: boolean };
  logo: string;
  light: Palette;
  dark: Palette;
  /** Plain feature sections: they use the email's background instead of their own colour. */
  flatFeatured?: boolean;
  /** A small "View in browser" link above the email (on unless turned off). */
  webLink?: boolean;
}

export interface Section {
  id: string;
  type: string;
  settings: Record<string, string>;
}

export interface CampaignDocument {
  schema: 2;
  brand: Brand;
  sections: Section[];
}

export interface Campaign extends CampaignSummary {
  preheader: string;
  from_name: string;
  from_email: string;
  reply_to: string;
  lists: string[];
  tags: string[];
  document: CampaignDocument;
}

export type FieldKind = 'text' | 'textarea' | 'url' | 'image' | 'number' | 'select' | 'toggle' | 'colour';

export interface FieldDef {
  key: string;
  label: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  options?: Array<[string, string]>;
  fallback?: string;
  /** Help shown under the field. */
  hint?: string;
  /** Hidden while the named settings have these values, e.g. {"imageStretch": "on"} or {"colour": ["a", "b"]}. */
  hideWhen?: Record<string, string | string[]>;
}

export interface SectionDef {
  label: string;
  icon: string;
  fields: FieldDef[];
  defaults: Record<string, string>;
}

export interface Preset {
  label: string;
  description: string;
  brand: Partial<Pick<Brand, 'fonts' | 'radius' | 'padding' | 'fontSize' | 'button' | 'flatFeatured'>>;
}

export interface SectionLibrary {
  sections: Record<string, SectionDef>;
  presets: Record<string, Preset>;
}

export type TemplateCategory = 'newsletter' | 'promotion' | 'event' | 'announcement' | 'welcome' | 'other';
export type TemplateColours = 'template' | 'brand';

/** A designed email to start a campaign from (see server/mailstudio/gallery.py). */
export interface EmailTemplate {
  format: 'mailstudio-template';
  version: 1;
  id: string;
  name: string;
  description: string;
  category: TemplateCategory;
  tags: string[];
  author: string;
  subject: string;
  preheader: string;
  document: CampaignDocument;
}

/** An online gallery entry: the full template is fetched when it's previewed or used. */
export interface GalleryEntry {
  id: string;
  name: string;
  description: string;
  category: TemplateCategory;
  tags: string[];
  author: string;
  preview: string;
}

export interface TemplateList {
  templates: EmailTemplate[];
  categories: TemplateCategory[];
  gallery: boolean;
}

export interface RenderedEmail {
  subject: string;
  html: string;
  text: string;
}

export interface Asset {
  id: string;
  url: string;
  width: number;
  height: number;
  bytes: number;
}

/** Upload a file as multipart form data (used for images). */
export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(`/api/v1${path}`, { method: 'POST', body: form, headers, credentials: 'same-origin' });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, (data ?? null) as Record<string, unknown> | null);
  return data as T;
}

export type MemberStatus = 'pending' | 'subscribed' | 'unsubscribed';

export interface Contact {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  fields: Record<string, string | number | boolean | null>;
  tags: string[];
  lists: Array<{ list_id: string; status: MemberStatus }>;
  suppressed: 'bounce' | 'complaint' | 'manual' | null;
  created_at: string;
  updated_at: string;
}

export interface ConsentEvent {
  action: 'consent' | 'confirm' | 'unsubscribe';
  source: string;
  ip: string | null;
  list_id: string | null;
  list_name: string | null;
  created_at: string;
}

export interface MailingList {
  id: string;
  name: string;
  description: string;
  double_opt_in: boolean;
  signup: SignupForm;
  counts: { subscribed: number; pending: number; unsubscribed: number };
  created_at: string;
  updated_at: string;
}

export interface SignupForm {
  enabled: boolean;
  title: string;
  intro: string;
  button: string;
  ask_name: boolean;
  thanks: string;
  redirect_url: string;
}

export interface Member {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  status: MemberStatus;
  subscribed_at: string | null;
  unsubscribed_at: string | null;
}

export interface Suppression {
  email: string;
  reason: 'bounce' | 'complaint' | 'manual';
  detail: string;
  created_at: string;
}

export interface ImportResult {
  rows: number;
  created: number;
  updated: number;
  added_to_list: number;
  skipped_unsubscribed: number;
  skipped_suppressed: number;
  invalid: number;
  problems: Array<{ row: number; error: string }>;
}

export interface CampaignStats {
  status: CampaignSummary['status'];
  total: number;
  queued: number;
  sent: number;
  failed: number;
  skipped: number;
  delivered: number;
  bounced: number;
  complained: number;
  unsubscribed: number;
}

export interface Settings {
  business: { name: string; address: string };
  sender: { from_name: string; from_email: string | null; reply_to: string | null };
  brand: Brand;
  smtp: { host: string; port: number; username: string; security: 'starttls' | 'tls' | 'none'; password_set: boolean };
  sending: { rate_per_second: number };
  ses: { region: string; access_key_id: string; configuration_set: string; notification_topic_arn: string; secret_set: boolean };
  provider: { active: ProviderName | '' };
  postmark: { message_stream: string; own_unsubscribes: boolean; server_token_set: boolean };
  mailgun: { domain: string; region: 'us' | 'eu'; api_key_set: boolean; webhook_signing_key_set: boolean };
  smtp2go: { api_key_set: boolean };
  relay: {
    host: string; port: number; username: string; security: 'starttls' | 'tls' | 'none'; bounce_address: string | null;
    imap_host: string; imap_port: number; imap_username: string; password_set: boolean; imap_password_set: boolean;
  };
  base_url: string;
}

export type ProviderName = 'postmark' | 'mailgun' | 'smtp2go' | 'authsmtp' | 'smtp' | 'ses';

export interface ConnectionCheck {
  provider: ProviderName;
  ok: boolean;
  message: string;
}

export interface SendingAccount {
  provider: string | null;
  /** The service's name for people, like "Postmark". */
  label: string | null;
  notifications: 'confirmed' | 'pending' | 'none' | 'unknown';
  /** Where Postmark, Mailgun or SMTP2GO should send bounces and spam reports. */
  webhook_url: string | null;
  /** SMTP relays: the last check of the bounce mailbox. */
  mailbox: { checked_at?: string; error?: string; last_count?: number } | null;
  ses: {
    region: string;
    production_access: boolean;
    sending_enabled: boolean;
    max_send_rate: number;
    max_24_hour_send: number;
    sent_last_24_hours: number;
  } | null;
  warnings: string[];
}

export interface SetupStatus {
  steps: Array<{ key: SetupStep; done: boolean }>;
  complete: boolean;
  provider: ProviderName | null;
  domain: string;
  base_url: string;
  https: boolean;
}

export type SetupStep = 'business' | 'sender' | 'provider' | 'domain' | 'notifications' | 'production' | 'audience';

export interface DnsRecord {
  purpose: string;
  type: 'CNAME' | 'TXT';
  host: string;
  value: string;
  status: 'ok' | 'missing' | 'wrong' | 'warning' | string;
  found: string[];
  help: string;
}

export interface DomainCheck {
  domain: string;
  provider: ProviderName | '';
  /** Where to verify the domain with the sending service (all but Amazon SES). */
  provider_note: string;
  registered_with_ses: boolean;
  ses_verified: boolean;
  dkim_status: string;
  ready: boolean;
  records: DnsRecord[];
}

/** Upload a CSV with extra form fields (used for contact import). */
export async function uploadForm<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(`/api/v1${path}`, { method: 'POST', body: form, headers, credentials: 'same-origin' });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, (data ?? null) as Record<string, unknown> | null);
  return data as T;
}

/** Every page of a list endpoint, for small collections like lists. */
export async function all<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;
  do {
    const sep = path.includes('?') ? '&' : '?';
    const page: Page<T> = await api<Page<T>>(`${path}${sep}limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
    items.push(...page.data);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}
