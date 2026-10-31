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
    // The theme control: a button that steps to the next theme, and a drop-down menu of them all.
    var wrap = el("div", "pf-topbar__themewrap");
    var rotator = el("button", "pf-topbar__theme");
    rotator.type = "button";
    rotator.appendChild(el("span")).setAttribute("data-theme-icon", "");
    var name = el("span", "pf-topbar__desktop");
    name.setAttribute("data-theme-name", "");
    rotator.appendChild(name);
    rotator.setAttribute("data-theme-cycle", "");
    wrap.appendChild(rotator);
    var choose = el("details", "pf-topbar__group pf-topbar__group--right pf-topbar__group--theme");
    var caret = el("summary");
    caret.setAttribute("aria-label", "Choose a theme");
    caret.setAttribute("title", "Choose a theme");
    var menu = el("div", "pf-topbar__menu");
    menu.setAttribute("data-theme-menu", "");
    choose.appendChild(caret);
    choose.appendChild(menu);
    choose.addEventListener("toggle", function () {
      if (choose.open) groups.forEach(function (other) { if (other !== choose) other.open = false; });
    });
    groups.push(choose);
    groups.forEach(function (other) {
      if (other === choose) return;
      other.addEventListener("toggle", function () { if (other.open) choose.open = false; });
    });
    wrap.appendChild(choose);
    bar.appendChild(wrap);
    if (window.PortfolioTheme && window.PortfolioTheme.bind) {
      window.PortfolioTheme.bind(rotator);
      window.PortfolioTheme.fill(menu);
    }
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
    // A sibling of <body>, not a child: a page that narrows its body (max-width, padding)
    // must not narrow the bar. Sticky positioning still works against the viewport.
    document.documentElement.insertBefore(bar, document.body);
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
