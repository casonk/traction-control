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
| `contrast` | dark | Black and white with strong colours, for low vision and glare |

With no saved choice a page follows the system: `daylight` when the system prefers
light, `midnight` otherwise. A choice made with the switcher is saved in the
browser (`localStorage`, key `portfolio-theme`). `?theme=ember` on any URL picks a
theme for that visit and saves it. Each site is its own browser origin, so the
choice is saved per site, not once for every site.

## Tokens

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
