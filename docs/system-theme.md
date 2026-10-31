# System themes

The portfolio's web pages and generated reports share one set of colour themes,
defined once in [`themes/themes.toml`](../themes/themes.toml). A page never picks
a colour; it uses a named token, so switching the theme restyles every page that
follows this guide.

## The themes

| Theme | Scheme | Use |
|---|---|---|
| `midnight` | dark | Default dark; the Clockwork palette (bg `#0f1117`, accent `#5b7fff`) |
| `daylight` | light | Default light; bright rooms and print |
| `ember` | dark | Warm dark, easier on the eyes at night |
| `contrast-dark` | dark | High contrast dark: black and white with strong colours, for low vision and glare (formerly `contrast`; a saved `contrast` still resolves) |
| `contrast-light` | light | High contrast white: black on white with black outlines, for low vision and sunlight |
| `hulk` | dark | The Incredible Hulk: gamma green on deep purple, with a purple and green stripe under the top bar |
| `neon` | dark | Electric cyan, magenta and lime on near-black, with a four-colour neon stripe |
| `liberty` | light | Red, white and blue, like a Bomb Pop: navy accent, red status colour, and a red/white/blue stripe under the top bar |

With no saved choice a page follows the system: `daylight` when the system prefers
light, `midnight` otherwise. A choice made with the switcher is saved in the
browser (`localStorage`, key `portfolio-theme`). `?theme=ember` on any URL picks a
theme for that visit and saves it. Each site is its own browser origin, so the
choice is saved per site, not once for every site.

## Tokens

A theme may also set `stripe`, a CSS background for a 3px strip under the top bar (Liberty uses
a red, white and blue gradient); it is `transparent` elsewhere. Renaming a theme? Add the old
name under `[meta.aliases]` in `themes/themes.toml` so saved choices and `?theme=` links keep
working.

Every theme defines every token; `scripts/render_portfolio_theme.py` rejects a
theme with a missing token, a value that is not `#rrggbb`, or a default theme of
the wrong scheme.

| Token | Meaning |
|---|---|
| `--bg`, `--surface`, `--surface2` | page background, cards and panels, raised or hovered rows |
| `--border` | rules and outlines |
| `--text`, `--muted` | body text, secondary text |
| `--accent`, `--accent-dim`, `--accent-ink` | primary colour, its low-emphasis fill, and text on a solid accent fill |
| `--green`, `--red`, `--yellow`, `--purple` | status and chart colours |
| `--green-dim`, `--red-dim`, `--yellow-dim`, `--purple-dim` | fills that carry text in the matching colour |

Rules for pages:

- Write `var(--surface)`, never `#1a1d27`. A literal colour does not change with the theme.
- Put text on `--bg`, `--surface` or `--surface2` in `--text` or `--muted`; put a
  status colour on its own `-dim` fill. The tests hold these pairs to WCAG AA
  (4.5:1) in every theme.
- Use `--accent` for the primary action and selection, `--green`/`--red`/`--yellow`
  for pass/fail/warn, and `--purple` as a second chart series. Do not rely on
  colour alone for meaning; pair it with a word or a mark.
- Set nothing on `color-scheme` yourself; each theme declares it.

## Using the theme in a page

```html
<head>
  <style>/* contents of portfolio-theme.css */</style>
  <script>/* contents of portfolio-theme.js */</script>
</head>
<body>
  <select data-theme-switcher></select>
```

The top bar's theme control has two parts: a button that steps to the next theme on each
click, and a drop-down caret beside it that lists every theme as a menu (swatch, name and
light or dark), so a theme can be picked directly. Pages that build their own bar can get the
same by adding an empty `<div data-theme-menu></div>` (inside a `<details>`, so choosing a
theme closes it); the script fills it.

A top bar can offer a rotator instead of a menu: a `<button data-theme-cycle>`
steps to the next theme on each click (Midnight, Daylight, Ember, High contrast,
then Midnight again) and shows the current theme's icon and name. Put
`<span data-theme-icon></span><span data-theme-name></span>` inside it to control
the layout, or leave it empty and the script writes the text. Its label and tooltip
name the theme it will switch to. Other open tabs of the same site follow a change.

Generated reports and pages served from several places inline both files so the
page works from disk and behind a proxy. Place the script in `<head>` so the theme
is applied before first paint. `window.PortfolioTheme.set("ember")` switches from
code. A chart drawn as a standalone SVG cannot see the page's theme; use
`var(--token, #fallback)` with the Midnight value as the fallback, and inline the
SVG when it should follow the theme.

## The top bar for services

Every service behind the shared Caddy can show the portfolio top bar (Home, Clockwork,
Lists, Media, Services, and the theme rotator) with one line in its page template:

```html
<script src="/_portfolio/chrome.js" defer></script>
```

`themes/portfolio-chrome.js` is one self-contained script: it applies the theme,
defines the `--pf-*` tokens on the bar only (so the page keeps its own palette until it
adopts the tokens), builds the bar, and loads `/_portfolio/nav.json`. wiring-harness's
Caddy serves `/_portfolio/` from a shared directory on every proxied site; Clockwork
writes `nav.json` (and the script) into it. If the file or the directory is missing the
bar still shows the rotator, from the last navigation cached in the browser.

`nav.json`:

```json
{"home": {"label": "Home", "icon": "🏠", "url": "https://…/home"},
 "items": [
   {"label": "Clockwork", "mobile": "Jobs", "icon": "⏰", "url": "https://…/"},
   {"label": "Lists", "icon": "✅", "menu": [
     {"label": "Groceries", "icon": "🛒", "url": "https://…/groceries"}]},
   {"label": "Services", "mobile": "Sites", "icon": "🧰", "align": "right", "menu": [
     {"section": "Tools"},
     {"label": "Bench", "icon": "🏁", "url": "https://…/", "service": true}]}]}
```

A menu entry with `"service": true` is highlighted when the page is on that site. Only
`http` and `https` URLs are used. The script does nothing on a page that already has a
Clockwork nav or a `.pf-topbar`, and nothing when the page has no `<body>`.

Pages that serve their own copy of the top bar markup instead should use
`themes/topbar.css` and `themes/topbar.js` as the reference for the styling.

## Making a service follow the theme

The top bar changes only itself. To make the page follow the theme, load the tokens and
use them in the page's CSS. Clockwork publishes both files next to `chrome.js`:

```html
<link rel="stylesheet" href="/_portfolio/theme.css">
<script src="/_portfolio/theme.js"></script>   <!-- synchronous: applies the saved theme before first paint -->
```

Then map the page's colours to tokens by role instead of by value:

| Page colour | Token |
|---|---|
| page background | `var(--bg)` |
| cards, panels, inputs | `var(--surface)`; raised or hovered `var(--surface2)` |
| rules and outlines | `var(--border)` |
| text, secondary text | `var(--text)`, `var(--muted)` |
| primary action; text on it | `var(--accent)`; `var(--accent-ink)` |
| status and series hues | `var(--green)` `var(--red)` `var(--yellow)` `var(--purple)` |
| a tinted fill carrying text in that hue | the matching `-dim` token |
| translucent tints | `color-mix(in srgb, var(--accent) 12%, transparent)` |

Rules that keep it working: delete the page's own dark-mode media query (the theme
replaces it); delete or rename page variables that share a token's name (`--bg: var(--bg)`
is a cycle) and alias the rest (`--panel: var(--surface)`); leave black shadows and scrims
alone; for data that needs its own hue, set `--hue` and mix it with tokens. Without the two
tags (direct access, no Caddy) tokens are undefined and the page falls back to the browser's
defaults, so keep the tags in every page head. A test in each repository keeps literal colours
out of its CSS (see crew-chief's `tests/test_theme.py`).

## Changing or adding a theme

1. Edit `themes/themes.toml` (add a `[themes.<name>]` table with every token).
2. Run `python3 scripts/render_portfolio_theme.py` and commit the toml, the CSS and
   the JS together. `--check` (a pre-commit hook) fails on stale outputs.
3. `python3 -m unittest discover -s tests -p test_portfolio_theme.py` checks the
   contrast pairs and the completeness of every theme.
4. Consumers refresh their copies with
   `python3 scripts/render_portfolio_theme.py --install <repo>/<dir>`.

## Adopting it in a repository

Vendor the two generated files (`--install`), inline them into the pages, use only
tokens in the page CSS, and add a test that the vendored files equal
`themes/portfolio-theme.{css,js}` whenever traction-control sits next to the
repository. `crew-chief` is the reference consumer. The Clockwork pages predate
the themes and still carry their own copy of the Midnight values; moving them to
tokens is tracked in `BACKLOG.md`.
