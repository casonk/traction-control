/* Generated from themes/themes.toml by scripts/render_portfolio_theme.py; do not edit. */
(function () {
  if (window.__portfolioChrome) return;
  window.__portfolioChrome = true;
  var style = document.createElement("style");
  style.textContent = ".pf-topbar {\n  --pf-bg: #0f1117;\n  --pf-surface: #1a1d27;\n  --pf-surface2: #22263a;\n  --pf-border: #2e3250;\n  --pf-text: #e2e4f0;\n  --pf-muted: #8890b0;\n  --pf-accent: #5b7fff;\n  --pf-accent-dim: #161c34;\n  --pf-accent-ink: #0f1117;\n  --pf-green: #3ecf6e;\n  --pf-green-dim: #1a4d30;\n  --pf-red: #e05252;\n  --pf-red-dim: #251418;\n  --pf-yellow: #e0b832;\n  --pf-yellow-dim: #4a3d10;\n  --pf-purple: #a06fff;\n  --pf-purple-dim: #29194c;\n}\n@media (prefers-color-scheme: light) {\n  html:not([data-theme]) .pf-topbar {\n    --pf-bg: #f4f5fa;\n    --pf-surface: #ffffff;\n    --pf-surface2: #eceef6;\n    --pf-border: #d3d7e6;\n    --pf-text: #1c2033;\n    --pf-muted: #5b6486;\n    --pf-accent: #3b5bdb;\n    --pf-accent-dim: #e0e8ff;\n    --pf-accent-ink: #ffffff;\n    --pf-green: #17743d;\n    --pf-green-dim: #d3f2df;\n    --pf-red: #c92a2a;\n    --pf-red-dim: #ffe6e6;\n    --pf-yellow: #8a5a00;\n    --pf-yellow-dim: #fff3bf;\n    --pf-purple: #7048e8;\n    --pf-purple-dim: #eee8ff;\n  }\n}\nhtml[data-theme=\"midnight\"] .pf-topbar {\n  --pf-bg: #0f1117;\n  --pf-surface: #1a1d27;\n  --pf-surface2: #22263a;\n  --pf-border: #2e3250;\n  --pf-text: #e2e4f0;\n  --pf-muted: #8890b0;\n  --pf-accent: #5b7fff;\n  --pf-accent-dim: #161c34;\n  --pf-accent-ink: #0f1117;\n  --pf-green: #3ecf6e;\n  --pf-green-dim: #1a4d30;\n  --pf-red: #e05252;\n  --pf-red-dim: #251418;\n  --pf-yellow: #e0b832;\n  --pf-yellow-dim: #4a3d10;\n  --pf-purple: #a06fff;\n  --pf-purple-dim: #29194c;\n}\nhtml[data-theme=\"daylight\"] .pf-topbar {\n  --pf-bg: #f4f5fa;\n  --pf-surface: #ffffff;\n  --pf-surface2: #eceef6;\n  --pf-border: #d3d7e6;\n  --pf-text: #1c2033;\n  --pf-muted: #5b6486;\n  --pf-accent: #3b5bdb;\n  --pf-accent-dim: #e0e8ff;\n  --pf-accent-ink: #ffffff;\n  --pf-green: #17743d;\n  --pf-green-dim: #d3f2df;\n  --pf-red: #c92a2a;\n  --pf-red-dim: #ffe6e6;\n  --pf-yellow: #8a5a00;\n  --pf-yellow-dim: #fff3bf;\n  --pf-purple: #7048e8;\n  --pf-purple-dim: #eee8ff;\n}\nhtml[data-theme=\"ember\"] .pf-topbar {\n  --pf-bg: #1a1614;\n  --pf-surface: #241e1b;\n  --pf-surface2: #2e2622;\n  --pf-border: #44382f;\n  --pf-text: #ece3da;\n  --pf-muted: #a8998c;\n  --pf-accent: #e8913a;\n  --pf-accent-dim: #4d3314;\n  --pf-accent-ink: #1a1614;\n  --pf-green: #7cc27a;\n  --pf-green-dim: #25422a;\n  --pf-red: #e5655b;\n  --pf-red-dim: #3b1c19;\n  --pf-yellow: #e6c04a;\n  --pf-yellow-dim: #4a3d12;\n  --pf-purple: #b98ae0;\n  --pf-purple-dim: #3d2a55;\n}\nhtml[data-theme=\"contrast\"] .pf-topbar {\n  --pf-bg: #000000;\n  --pf-surface: #0b0b0b;\n  --pf-surface2: #1c1c1c;\n  --pf-border: #ffffff;\n  --pf-text: #ffffff;\n  --pf-muted: #d6d6d6;\n  --pf-accent: #ffd400;\n  --pf-accent-dim: #4d4000;\n  --pf-accent-ink: #000000;\n  --pf-green: #4dff88;\n  --pf-green-dim: #003d1a;\n  --pf-red: #ff6b6b;\n  --pf-red-dim: #520000;\n  --pf-yellow: #ffd400;\n  --pf-yellow-dim: #4d4000;\n  --pf-purple: #d6a8ff;\n  --pf-purple-dim: #3d1a66;\n}\n/* Portfolio top bar. Styled only with --pf-* tokens, which the generator defines on\n   .pf-topbar for every theme, so a page keeps its own palette and the bar still themes. */\n.pf-topbar, .pf-topbar *, .pf-topbar *::before, .pf-topbar *::after { box-sizing: border-box; }\n.pf-topbar {\n  position: sticky;\n  top: 0;\n  z-index: 2147483000;\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  height: 52px;\n  margin: 0;\n  padding: 0 20px;\n  color: var(--pf-text);\n  background: var(--pf-surface);\n  border-bottom: 1px solid var(--pf-border);\n  font: 600 13px/1.4 -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;\n  text-align: left;\n}\n.pf-topbar a, .pf-topbar summary { color: inherit; text-decoration: none; }\n.pf-topbar__brand, .pf-topbar__direct, .pf-topbar summary, .pf-topbar__theme {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  min-height: 36px;\n  padding: 6px 10px;\n  border-radius: 7px;\n  white-space: nowrap;\n}\n.pf-topbar__brand { flex: none; font-weight: 700; }\n.pf-topbar__mobile { display: none; }\n.pf-topbar__groups { display: flex; align-items: center; gap: 4px; min-width: 0; }\n.pf-topbar__group { position: relative; }\n.pf-topbar summary { cursor: pointer; list-style: none; }\n.pf-topbar summary::-webkit-details-marker { display: none; }\n.pf-topbar summary::after { content: \"\\25BE\"; font-size: 10px; opacity: .7; }\n.pf-topbar__brand:hover, .pf-topbar__direct:hover, .pf-topbar summary:hover,\n.pf-topbar__group[open] summary, .pf-topbar [aria-current=\"page\"] {\n  color: var(--pf-text);\n  background: var(--pf-accent-dim);\n}\n.pf-topbar :focus-visible { outline: 2px solid var(--pf-accent); outline-offset: 2px; }\n.pf-topbar__menu {\n  position: absolute;\n  top: calc(100% + 7px);\n  left: 0;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 190px;\n  max-width: min(340px, calc(100vw - 20px));\n  max-height: min(70vh, 560px);\n  padding: 7px;\n  overflow-y: auto;\n  background: var(--pf-surface2);\n  border: 1px solid var(--pf-border);\n  border-radius: 9px;\n  box-shadow: 0 12px 30px rgba(0, 0, 0, .5);\n}\n.pf-topbar__group:not([open]) .pf-topbar__menu { display: none; }\n.pf-topbar__group--right .pf-topbar__menu { left: auto; right: 0; min-width: 240px; }\n.pf-topbar__menu a { display: block; padding: 9px 11px; border-radius: 6px; font-weight: 500; white-space: normal; overflow-wrap: anywhere; }\n.pf-topbar__menu a:hover, .pf-topbar__menu a:focus-visible { background: var(--pf-accent-dim); }\n.pf-topbar__section { padding: 10px 11px 3px; color: var(--pf-muted); font-size: 10px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }\n.pf-topbar__section:not(:first-child) { border-top: 1px solid var(--pf-border); margin-top: 5px; }\n.pf-topbar__theme {\n  flex: none;\n  margin-left: auto;\n  color: inherit;\n  background: none;\n  border: 1px solid var(--pf-border);\n  font: inherit;\n  cursor: pointer;\n}\n.pf-topbar__theme:hover { color: var(--pf-text); background: var(--pf-accent-dim); }\n@media (max-width: 600px) {\n  .pf-topbar { flex-direction: column; align-items: stretch; justify-content: center; gap: 0; height: 88px; padding: 4px 10px; }\n  .pf-topbar__brand { align-self: flex-start; min-height: 30px; padding: 2px 8px; }\n  .pf-topbar__desktop { display: none; }\n  .pf-topbar__mobile { display: inline; }\n  .pf-topbar__groups { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 3px; width: 100%; }\n  .pf-topbar__direct, .pf-topbar summary { justify-content: center; min-height: 36px; padding: 4px 2px; font-size: 12px; }\n  .pf-topbar__theme { position: absolute; top: 4px; right: 10px; min-height: 30px; margin: 0; padding: 2px 8px; }\n  .pf-topbar__menu, .pf-topbar__group--right .pf-topbar__menu { position: fixed; top: 88px; left: 10px; right: 10px; min-width: 0; max-width: none; max-height: min(65vh, 500px); }\n}\n";
  document.head.appendChild(style);
})();
if (!window.PortfolioTheme) {
/* Generated from themes/themes.toml by scripts/render_portfolio_theme.py; do not edit. */
(function () {
  var THEMES = {
  "midnight": {
    "label": "Midnight",
    "scheme": "dark",
    "icon": "\ud83c\udf19"
  },
  "daylight": {
    "label": "Daylight",
    "scheme": "light",
    "icon": "\u2600\ufe0f"
  },
  "ember": {
    "label": "Ember",
    "scheme": "dark",
    "icon": "\ud83d\udd25"
  },
  "contrast": {
    "label": "High contrast",
    "scheme": "dark",
    "icon": "\u25d0"
  }
};
  var KEY = "portfolio-theme";
  var DARK = "midnight", LIGHT = "daylight";
  var root = document.documentElement;
  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function pick() {
    var asked = new URLSearchParams(location.search).get("theme");
    if (asked && THEMES[asked]) return asked;
    var kept = saved();
    if (kept && THEMES[kept]) return kept;
    return window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? LIGHT : DARK;
  }
  var ORDER = Object.keys(THEMES);
  function current() { return root.getAttribute("data-theme"); }
  function following(name) { return ORDER[(ORDER.indexOf(name) + 1) % ORDER.length]; }
  function paintCycle(el, name) {
    var theme = THEMES[name], target = THEMES[following(name)];
    var icon = el.querySelector("[data-theme-icon]"), label = el.querySelector("[data-theme-name]");
    if (icon || label) {
      if (icon) icon.textContent = theme.icon;
      if (label) label.textContent = theme.label;
    } else {
      el.textContent = theme.icon + " " + theme.label;
    }
    var text = "Theme: " + theme.label + ". Switch to " + target.label + ".";
    el.setAttribute("aria-label", text);
    el.setAttribute("title", text);
  }
  function apply(name) {
    root.setAttribute("data-theme", name);
    document.querySelectorAll("[data-theme-switcher]").forEach(function (el) { el.value = name; });
    document.querySelectorAll("[data-theme-cycle]").forEach(function (el) { paintCycle(el, name); });
  }
  function set(name) {
    if (!THEMES[name]) return;
    try { localStorage.setItem(KEY, name); } catch (e) {}
    apply(name);
  }
  function cycle() { set(following(current() || pick())); }
  function bind(el) {
    if (el.pfBound) return;
    el.pfBound = true;
    el.addEventListener("click", cycle);
    paintCycle(el, current() || pick());
  }
  apply(pick());
  window.addEventListener("storage", function (event) {
    if (event.key === KEY && THEMES[event.newValue]) apply(event.newValue);
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-cycle]").forEach(bind);
    document.querySelectorAll("[data-theme-switcher]").forEach(function (el) {
      if (el.tagName === "SELECT" && !el.options.length) {
        Object.keys(THEMES).forEach(function (name) {
          var option = document.createElement("option");
          option.value = name;
          option.textContent = THEMES[name].label;
          el.appendChild(option);
        });
      }
      el.setAttribute("aria-label", "Theme");
      el.addEventListener("change", function () { set(el.value); });
    });
    apply(root.getAttribute("data-theme") || pick());
  });
  window.PortfolioTheme = { themes: THEMES, set: set, cycle: cycle, bind: bind, current: current };
})();
}
/* Portfolio top bar: builds the shared navigation from /_portfolio/nav.json (or
   window.PORTFOLIO_NAV_URL) and a theme rotator. Runs inside the chrome bundle. */
(function () {
  var NAV_URL = window.PORTFOLIO_NAV_URL || "/_portfolio/nav.json";
  var CACHE = "portfolio-nav-cache";

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function safe(url) {
    try {
      var parsed = new URL(url, location.href);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed : null;
    } catch (e) { return null; }
  }
  function here(url) {
    var parsed = safe(url);
    if (!parsed || parsed.origin !== location.origin) return false;
    var path = location.pathname.replace(/\/+$/, "") || "/";
    var target = parsed.pathname.replace(/\/+$/, "") || "/";
    return target === "/" ? path === "/" : path === target || path.indexOf(target + "/") === 0;
  }
  function sameSite(url) {
    var parsed = safe(url);
    return !!parsed && parsed.origin === location.origin;
  }
  function link(item, cls) {
    var parsed = safe(item.url);
    if (!parsed) return null;
    var a = el("a", cls);
    a.href = parsed.href;
    a.textContent = (item.icon ? item.icon + " " : "") + item.label;
    if (here(item.url)) a.setAttribute("aria-current", "page");
    return a;
  }
  function labelled(item, cls) {
    var a = link({ url: item.url, label: "", icon: item.icon }, cls);
    if (!a) return null;
    a.textContent = item.icon ? item.icon + " " : "";
    a.appendChild(el("span", "pf-topbar__desktop", item.label));
    a.appendChild(el("span", "pf-topbar__mobile", item.mobile || item.label));
    if (here(item.url)) a.setAttribute("aria-current", "page");
    return a;
  }
  function group(item, groups) {
    var details = el("details", "pf-topbar__group" + (item.align === "right" ? " pf-topbar__group--right" : ""));
    var summary = el("summary");
    summary.appendChild(document.createTextNode(item.icon ? item.icon + " " : ""));
    summary.appendChild(el("span", "pf-topbar__desktop", item.label));
    summary.appendChild(el("span", "pf-topbar__mobile", item.mobile || item.label));
    var menu = el("div", "pf-topbar__menu");
    var current = false;
    (item.menu || []).forEach(function (entry) {
      if (entry.section) { menu.appendChild(el("div", "pf-topbar__section", entry.section)); return; }
      var a = link(entry);
      if (!a) return;
      if (sameSite(entry.url) && entry.service) { a.setAttribute("aria-current", "page"); current = true; }
      menu.appendChild(a);
    });
    if (current) summary.setAttribute("aria-current", "page");
    details.appendChild(summary);
    details.appendChild(menu);
    details.addEventListener("toggle", function () {
      if (details.open) groups.forEach(function (other) { if (other !== details) other.open = false; });
    });
    groups.push(details);
    return details;
  }
  function render(bar, nav) {
    bar.textContent = "";
    var groups = [];
    if (nav.home) {
      var home = link(nav.home, "pf-topbar__brand");
      if (home) bar.appendChild(home);
    }
    var wrap = el("div", "pf-topbar__groups");
    (nav.items || []).forEach(function (item) {
      var node = item.menu ? group(item, groups) : labelled(item, "pf-topbar__direct");
      if (node) wrap.appendChild(node);
    });
    bar.appendChild(wrap);
    var rotator = el("button", "pf-topbar__theme");
    rotator.type = "button";
    rotator.appendChild(el("span")).setAttribute("data-theme-icon", "");
    var name = el("span", "pf-topbar__desktop");
    name.setAttribute("data-theme-name", "");
    rotator.appendChild(name);
    rotator.setAttribute("data-theme-cycle", "");
    bar.appendChild(rotator);
    if (window.PortfolioTheme && window.PortfolioTheme.bind) window.PortfolioTheme.bind(rotator);
    bar.pfGroups = groups;
  }
  function cached() {
    try { return JSON.parse(localStorage.getItem(CACHE)); } catch (e) { return null; }
  }
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }
  ready(function () {
    if (document.querySelector(".cw-site-nav, .pf-topbar") || !document.body) return;
    var bar = el("nav", "pf-topbar");
    bar.setAttribute("aria-label", "Site navigation");
    document.body.insertBefore(bar, document.body.firstChild);
    render(bar, cached() || {});
    fetch(NAV_URL, { credentials: "same-origin", cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (nav) {
        try { localStorage.setItem(CACHE, JSON.stringify(nav)); } catch (e) {}
        render(bar, nav);
      })
      .catch(function () {});
    document.addEventListener("click", function (event) {
      if (!bar.contains(event.target)) (bar.pfGroups || []).forEach(function (g) { g.open = false; });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      var open = (bar.pfGroups || []).filter(function (g) { return g.open; })[0];
      if (open) { open.open = false; open.querySelector("summary").focus(); }
    });
  });
})();
