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
  apply(pick());
  window.addEventListener("storage", function (event) {
    if (event.key === KEY && THEMES[event.newValue]) apply(event.newValue);
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-theme-cycle]").forEach(function (el) {
      el.addEventListener("click", cycle);
    });
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
  window.PortfolioTheme = { themes: THEMES, set: set, cycle: cycle, current: current };
})();
