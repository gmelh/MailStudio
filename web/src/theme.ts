/**
 * Light or dark appearance for the app itself (not the emails being designed).
 * Until someone picks one, the app follows the computer's own setting.
 * index.html applies the remembered choice before the page first paints, so there's no flash.
 */

import { useEffect } from 'preact/hooks';
import { getPref, hasPref, oneOf, setPref, usePref } from './prefs';

export type Theme = 'light' | 'dark';
const KEY = 'theme';
const valid = oneOf<Theme>('light', 'dark');
const systemQuery = () => matchMedia('(prefers-color-scheme: dark)');
const system = (): Theme => (systemQuery().matches ? 'dark' : 'light');

/** Earlier versions stored the word itself under "mailstudio:ui-theme"; carry it over once. */
function migrate(): void {
  try {
    const old = localStorage.getItem('mailstudio:ui-theme');
    if (old === 'light' || old === 'dark') setPref(KEY, old);
    localStorage.removeItem('mailstudio:ui-theme');
  } catch {
    // Storage unavailable: nothing to carry over.
  }
}

function apply(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/** At start-up: apply the remembered theme, and follow the computer's setting until one is chosen. */
export function startTheme(): void {
  migrate();
  apply(getPref(KEY, system(), valid));
  systemQuery().addEventListener('change', () => {
    if (!hasPref(KEY)) apply(system());
  });
}

/** The current theme and a way to change (and remember) it. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = usePref<Theme>(KEY, system(), valid);
  useEffect(() => apply(theme), [theme]);
  return [theme, setTheme];
}
