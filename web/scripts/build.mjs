// Builds the web app into dist/, which the Mail Studio server serves.
//   node scripts/build.mjs           production build (minified)
//   node scripts/build.mjs --watch   rebuild on change; run `mailstudio serve` alongside
import { build, context } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';

const watch = process.argv.includes('--watch');
const options = {
  entryPoints: ['src/main.tsx'],
  bundle: true,
  format: 'esm',
  target: 'es2022',
  outfile: 'dist/app.js',
  jsx: 'automatic',
  jsxImportSource: 'preact',
  sourcemap: true,
  minify: !watch,
  logLevel: 'info',
};

await mkdir('dist', { recursive: true });
await copyFile('index.html', 'dist/index.html');
await copyFile('src/styles.css', 'dist/styles.css');
if (watch) {
  await (await context(options)).watch();
} else {
  await build(options);
}
