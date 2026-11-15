# MailStudio web app

Preact + TypeScript, bundled with esbuild. The server serves the build from `web/dist`,
on the same address as the API, so there's nothing extra to deploy.

```sh
npm install
npm run build       # dist/
npm run dev         # rebuild on change; run `mailstudio serve` alongside and open http://127.0.0.1:8080
npm run typecheck
```

| File | What's there |
| --- | --- |
| `src/app.tsx` | Chooses the setup, login or main screen |
| `src/api.ts` | API calls: session cookie, CSRF header, problem-details errors |
| `src/router.tsx` | Address-bar routing (`navigate`, `Link`, `match`) |
| `src/session.tsx` | The logged-in user, for every screen |
| `src/ui.tsx` | Shared pieces: fields, buttons, notices, loading, formatting |
| `src/pages/` | One file per screen: campaigns (list, send, report), contacts (list, detail, import, never-emailed), lists, settings, users |
| `src/editor/CampaignEditor.tsx` | The email editor page: loading, autosave, layout, toolbar |
| `src/editor/model.ts` | Pure functions that change a campaign document (add, move, duplicate, presets…) |
| `src/editor/Outline.tsx` | Left panel: the sections (drag to reorder) and the library to add from |
| `src/editor/Preview.tsx` | The live preview: rendered by the server, shown in a sandboxed frame, click to select |
| `src/editor/Inspector.tsx` | Right panel: section settings, email details, brand and style |
| `src/editor/fields.tsx` | One input per field kind, merge-tag menu, image upload |
| `src/styles.css` | `--ui-*` colour tokens, light and dark |

The editor's preview is rendered by the server (`POST /api/v1/render`) a quarter-second
after each change, with exactly the code that renders the sent email, so what you see is
what gets sent. Section types come from `GET /api/v1/sections`, read from `sections/`.
