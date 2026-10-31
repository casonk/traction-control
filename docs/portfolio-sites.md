# Portfolio sites: deploying pages with whatever tooling is present

Each repository that serves web pages can show the portfolio theme and top bar on its own, whichever
traction-control profile and whichever web front end the machine has. This page describes how, and
how private repositories take part without disclosing anything. For the colours and the top bar
themselves see [the system theme guide](system-theme.md).

## Two axes, one page contract

A page never depends on what surrounds it. It always asks for the same four files:

```html
<link rel="stylesheet" href="/_portfolio/theme.css">
<script src="/_portfolio/theme.js"></script>      <!-- synchronous: theme before first paint -->
<script src="/_portfolio/chrome.js" defer></script>   <!-- the top bar; also loads /_portfolio/nav.json -->
```

**Agent profile (what tools exist).** Profiles are cumulative: `light` has traction-control and
Clockwork, `moderate` adds archility and tachometer, `heavy` adds auto-pass and shock-relay.
wiring-harness is in none of them, so the minimum suite is traction-control and Clockwork with no
Caddy. Pages must work there and at every profile above it.

**Front end (who answers `/_portfolio/*`).** The wiring-harness registry has three `ingress` values:

| Ingress | Who answers `/_portfolio/*` |
|---|---|
| `wiring-harness-caddy` | The shared Caddy, from the shared chrome directory (`/srv/portfolio-chrome`) |
| `repo-caddy` | The repository's own Caddy drop-in, with one line: `import portfolio_chrome <dir>` (a snippet the generated Caddyfile defines) |
| `direct` (or no front end) | The application itself, from `portfolio_site.py` |

Behind either Caddy the application is never asked. Without one, the vendored `portfolio_site.py`
answers from the first place that has the file: the central store, then the repository's own
fallback copy. So the same HTML works everywhere.

## The central store

traction-control owns the store, `~/.config/traction-control/sites/` (owner-only):

```
sites/
  _shared/                  theme.css theme.js chrome.js (published by traction-control)
                            nav.json (written by Clockwork when it runs)
  <owner>__<repo>/          one entry per enrolled repository, named by its slug
    site.local.toml         fronting, hostname, port (from the wiring-harness registry if present)
    assets -> ../_shared
```

Each enrolled checkout gets one ignored symlink, `.portfolio`, to its entry. Private details
(hostname, port, private slugs) exist only in these owner-only files. Nothing here is tracked.

## How a repository finds its settings

`portfolio_site.resolve(repo_root, vendored=...)` never raises. It looks, in order, at:

1. `PORTFOLIO_SITE_DIR`
2. the checkout's `.portfolio` link
3. the store entry for the repository's own origin slug (the slug fallback, for a missing link)
4. the repository's tracked `config/portfolio-site.example.toml` (synthetic values only)
5. built-in defaults (`fronting = "direct"`)

Settings are layered (defaults, then the example config, then the store entry's `site.local.toml`).
Assets come from the entry's `assets/`, then from the repository's vendored fallback directory,
which is the last resort and is always present. `nav.json` falls back to an empty navigation, so the
bar shows just the theme control. A link or slug entry outside the store is ignored, only the four
names are served, and a file must be a regular file inside the directory it was looked up in.

## Commands

```bash
python3 scripts/portfolio_sites.py list                # who is enrolled and how they are fronted
python3 scripts/portfolio_sites.py publish [--apply]   # theme files -> the store's _shared (and --dir DIR)
python3 scripts/portfolio_sites.py link    [--apply]    # store entries and checkout links
python3 scripts/portfolio_sites.py vendor  [--apply] [NAME ...]   # each repo's fallback copy
python3 scripts/portfolio_sites.py check               # exit 1 on a missing link or stale copy
bash scripts/install_traction_control_agents.sh --tier light --link-sites [--activate]
```

Everything is a plan until `--apply` (for the bootstrap, until `--activate`). `link` refuses a
checkout that is missing, whose origin is a different repository, that does not ignore `/.portfolio`,
or whose `.portfolio` is a real file, and writes nothing for it.

## Enrolling repositories, public and private

`config/portfolio-sites/sites.conf` is tracked, so it may name only repositories the visibility
registry classifies **public**. A private repository is enrolled in an ignored overlay,
`~/.config/traction-control/portfolio-sites.private.local.conf` (owner-only, outside every work tree
or ignored in it), the same workflow as the private support bundle. Both use
`name|github_slug|relative_path|vendor_dir|fronting`. Every entry is verified against the registry
and the tool fails closed: a public slug in the overlay, a private one in the tracked file, an
unclassified one, or a world-readable overlay is refused. Output hides private slugs unless
`--show-slugs` is given. A synthetic template is `config/portfolio-sites/private-sites.example.conf`.

Theme assets are generic and safe to vendor anywhere. The private parts are `site.local.toml` and
`nav.json`, which stay in the owner-only store and are served only behind mTLS or the VPN.

## Adding a repository

1. Add `/.portfolio` to its `.gitignore` and a synthetic `config/portfolio-site.example.toml`
   (`[site]` with `fronting = "direct"`).
2. Enrol it (tracked file if public, overlay if private) and run `vendor --apply`, which writes
   `site.py`, the three fallback assets and a `MANIFEST.sha256` into its `vendor_dir`.
3. In its request handler, answer `/_portfolio/*` from
   `site = portfolio_site.resolve(repo_root, vendored=vendor_dir)` and `site.response(path)`.
   Add the three tags above to each page, and a test that the manifest matches the files.
4. `link --apply`, then `check`.

## Exceptions

Root-staged services (nordility) and pages whose security policy forbids external files (a sign-in
page) are not enrolled: they serve pinned real copies of the files from code they control, with no
symlinks, as nordility's installer does.
