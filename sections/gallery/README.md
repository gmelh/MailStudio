# Templates

A template is a whole designed email that someone starts a campaign from: its colours,
fonts and shape, its sections, and placeholder words that show what goes where. Choosing
**New campaign** opens a picker with:

- **Included**: the templates in this folder, which ship with every install.
- **mailstudio.email gallery**: templates published on the website. The MailStudio server
  fetches them (the browser never talks to the gallery), and only JSON is ever taken from it.
- **Open a template file…**: any template file someone has downloaded or been sent.

Each card is a live preview, rendered by the install itself with its own logo and business
details. People choose **Template colours** or **My brand colours** (the light and dark
palettes from Brand & style in Settings). The template's fonts, corners and spacing are
used either way, and everything can be changed afterwards in the editor.

In the editor, **Save as template** downloads the current campaign as a template file
(`<name>.mstemplate.json`). The logo, business name and address are left out, so whoever
uses it brings their own.

## The file format

One JSON file per template. The JSON Schema is
[`schema/template.schema.json`](../../schema/template.schema.json).

```json
{
  "format": "mailstudio-template",
  "version": 1,
  "id": "harbour",
  "name": "Harbour",
  "description": "A calm, editorial monthly newsletter in deep teal…",
  "category": "newsletter",
  "tags": ["monthly", "editorial"],
  "author": "MailStudio",
  "subject": "The Harbour Letter — this month's news",
  "preheader": "Three stories, one big idea, and what's coming next.",
  "document": { "schema": 2, "brand": { … }, "sections": [ … ] }
}
```

- `id`: lower-case letters, digits and hyphens. Unique within a gallery.
- `category`: one of `newsletter`, `promotion`, `event`, `announcement`, `welcome`,
  `other`. The picker filters by it.
- `document`: an ordinary campaign document, exactly what the editor saves. Easiest is to
  design the email in MailStudio and use **Save as template**, then fill in the
  description, category and tags.
- Leave out the header's `brand`, and the footer's `company` and `address`: they're filled
  in from Settings. The logo always comes from Settings too.

Files in this folder are loaded in name order, so they're numbered (`01-harbour.json`).

## Guidelines for a good template

These keep templates useful to anyone, and safe to publish:

1. **No images of our own.** Images belong to the person sending. A template works with
   just the logo from Settings, and sections with images (Hero image, Image + text,
   Products, Video) show the editor's placeholders until someone adds theirs.
2. **Both palettes.** Design the light *and* dark palettes, and check both in the picker.
   Body text should reach 4.5:1 contrast with its background (headings and buttons 3:1);
   `buttonText` must read clearly on `accent`.
3. **Words that teach.** Placeholder text should say what goes there and how to write it
   ("Open with the story your readers will care about most…"), never lorem ipsum.
4. **Links go to `https://example.com`**, so nothing points anywhere real by accident.
5. **One clear purpose and one main button.** A second button repeating the first is fine
   at the end of a long email.
6. **Feature backgrounds with care.** Two featured sections next to each other merge
   into one block; alternate them, or keep one off.
7. **Only standard sections and fonts**, so the template works on any install of the same
   version or later.

## Checking templates

From the `server` folder:

```bash
mailstudio check-templates ../sections/gallery             # validate and render every file
mailstudio check-templates my-template.json --out previews # also write the rendered HTML
```

It validates each file, makes sure it only uses sections this version knows, and renders
it light and dark just as the picker would. The test suite does the same for this folder.

## The online gallery (mailstudio.email)

The gallery is a folder of static files; any web host serves it:

```
gallery/
  index.json              the list the app reads
  templates/<id>.json     each template
  previews/<id>-light.html, <id>-dark.html   rendered previews for the website's pages
```

Build it from a folder of template files:

```bash
mailstudio build-gallery path/to/templates --out site/gallery --site https://mailstudio.email
```

It refuses to build if any template fails the check. The previews load the social icons
from the website's `/social/` folder, so copy `sections/social/*.png` there.

`index.json`:

```json
{
  "format": "mailstudio-gallery",
  "version": 1,
  "templates": [
    {
      "id": "harbour", "name": "Harbour", "description": "…", "category": "newsletter",
      "tags": ["monthly"], "author": "MailStudio",
      "file": "templates/harbour.json", "preview": "previews/harbour-light.html"
    }
  ]
}
```

`file` must be a path inside the gallery folder. The app fetches the index (cached for ten
minutes), then each template's file when it's previewed or used, and checks it again
before using it. Installs can point at another gallery with `MAILSTUDIO_GALLERY_URL`, or
turn it off by setting it empty.

### Growing the collection

A practical way to build the gallery up to dozens of templates:

- **Keep the gallery's templates in their own repository** (for example
  `mailstudio-templates`), one file per template, with CI running
  `mailstudio check-templates` on every pull request and `build-gallery` on merge to
  publish the site. Contributors send a template file; review is a look at the previews.
- **Fill the gaps by category.** Aim for three or four in each: newsletters (weekly,
  monthly, digest), sales and offers (sale, coupon, last chance), events (invitation,
  reminder, thank-you), announcements (launch, update, news), welcome (welcome,
  onboarding), plus the occasional seasonal one.
- **Vary the style, not just the colour.** The presets are a quick axis: Modern (sans,
  rounded), Editorial (serif, wide margins), Minimal (flat). Combine each purpose with
  two or three of them and a distinct palette.
- **Palette families.** A palette is eight colours for light and eight for dark. Keeping a
  small named set (Harbour teal, Ember coral, Meadow green, Ink violet, …) and reusing them
  across purposes makes the gallery look like a family.
- **The format is versioned.** `version` lets later releases add things (for example
  images hosted by the gallery, or more sections) without breaking older installs: an
  older install says it can't use a newer template instead of guessing.

## Included templates

| File | Name | Kind | Style |
| --- | --- | --- | --- |
| `01-harbour.json` | Harbour | Monthly newsletter | Editorial serif, deep teal |
| `02-ember.json` | Ember | Sale with a coupon code | Bold sans, rounded, warm coral |
| `03-meadow.json` | Meadow | Event invitation | Friendly, very rounded, fresh green |
| `04-ink.json` | Ink | Launch announcement | Minimal and flat, ink and violet |
| `05-postcard.json` | Postcard | Weekly digest | Bookish serif, cream and burgundy |
| `06-slate.json` | Slate | Company or team update | Businesslike, flat, steel blue |
| `07-citrus.json` | Citrus | Flash sale, last chance | Pill buttons, lemon and amber |
| `08-velvet.json` | Velvet | Members-only offer | Elegant serif, wide margins, plum |
| `09-lantern.json` | Lantern | Webinar invitation | Clean and professional, royal blue |
| `10-confetti.json` | Confetti | Thank-you after an event | Playful, very rounded, bright pink |
| `11-haven.json` | Haven | Welcome | Serif headings, gentle sky blue |
| `12-clay.json` | Clay | Onboarding, getting started | Practical, rounded, terracotta |
| `13-beacon.json` | Beacon | What's new, product update | Flat, black and yellow |
| `14-bulletin.json` | Bulletin | News or press announcement | Newspaper serif, flat, navy |
| `15-hearth.json` | Hearth | Seasonal greetings | Cosy serif, pine green and berry red |
| `16-marigold.json` | Marigold | Feedback or survey request | Friendly sans, rounded, marigold |

A test checks every bundled template's colours for readable contrast (WCAG AA) in both
light and dark mode.
