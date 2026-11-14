import { render } from 'preact';
import { App } from './app';
import { applyTheme, initialTheme } from './theme';

applyTheme(initialTheme());
render(<App />, document.getElementById('app')!);
