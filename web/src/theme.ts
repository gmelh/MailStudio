/** Light or dark appearance for the app itself (not the emails being designed). */

const KEY = 'mailstudio:ui-theme';
export type Theme = 'light' | 'dark';

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

export function initialTheme(): Theme {
  return stored() ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Storage unavailable (private mode): the choice lasts for this visit only.
  }
}
