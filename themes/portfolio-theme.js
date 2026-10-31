/* Generated from themes/themes.toml by scripts/render_portfolio_theme.py; do not edit. */
(function () {
  var THEMES = {
  "midnight": {
    "label": "Midnight",
    "scheme": "dark"
  },
  "daylight": {
    "label": "Daylight",
    "scheme": "light"
  },
  "ember": {
    "label": "Ember",
    "scheme": "dark"
  },
  "contrast": {
    "label": "High contrast",
    "scheme": "dark"
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
  function apply(name) {
    root.setAttribute("data-theme", name);
    document.querySelectorAll("[data-theme-switcher]").forEach(function (el) { el.value = name; });
  }
  function set(name) {
    if (!THEMES[name]) return;
    try { localStorage.setItem(KEY, name); } catch (e) {}
    apply(name);
  }
  apply(pick());
  document.addEventListener("DOMContentLoaded", function () {
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
  window.PortfolioTheme = { themes: THEMES, set: set, current: function () {
    return root.getAttribute("data-theme");
  } };
})();
