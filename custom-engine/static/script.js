/* PrivateSearch client script. Vanilla JS, no network calls, no cookies.
 * Preferences live only in localStorage and reach the server only as hidden form fields. */
(function () {
  "use strict";

  var KEYS = { theme: "ps.theme", safe: "ps.safe", lang: "ps.lang" };
  var root = document.documentElement;

  /* localStorage can throw (privacy mode, blocked storage): always wrap. */
  function load(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  /* ---------- Theme ---------- */

  function applyTheme(theme) {
    if (theme !== "light" && theme !== "dark") { theme = "dark"; }
    root.setAttribute("data-theme", theme);
    var labels = document.querySelectorAll("[data-theme-label]");
    for (var i = 0; i < labels.length; i++) {
      labels[i].textContent = theme === "dark" ? "Dark" : "Light";
    }
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    for (var j = 0; j < toggles.length; j++) {
      toggles[j].setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    }
  }

  /* ---------- Settings -> hidden inputs ---------- */

  function syncHidden(name, value) {
    var inputs = document.querySelectorAll('form input[type="hidden"][name="' + name + '"]');
    for (var i = 0; i < inputs.length; i++) { inputs[i].value = value; }
  }

  function applySafe(on) {
    syncHidden("safe", on ? "1" : "0");
    var box = document.querySelector('[data-setting="safe"]');
    if (box) { box.checked = on; }
    var text = document.querySelector("[data-safe-text]");
    if (text) { text.textContent = on ? "ON" : "OFF"; }
  }

  function applyLang(code) {
    syncHidden("lang", code);
    var sel = document.querySelector('[data-setting="lang"]');
    if (sel) { sel.value = code; }
  }

  /* ---------- Init ---------- */

  function init() {
    var theme = load(KEYS.theme);
    applyTheme(theme === "light" ? "light" : "dark");

    var storedSafe = load(KEYS.safe);
    var box = document.querySelector('[data-setting="safe"]');
    if (storedSafe === "1" || storedSafe === "0") {
      applySafe(storedSafe === "1");
    } else if (box) {
      applySafe(box.checked); /* server-rendered default */
    }

    var sel = document.querySelector('[data-setting="lang"]');
    var storedLang = load(KEYS.lang);
    if (sel && storedLang) {
      var found = false;
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === storedLang) { found = true; break; }
      }
      if (found) { applyLang(storedLang); }
    }

    /* Theme toggles */
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    for (var t = 0; t < toggles.length; t++) {
      toggles[t].addEventListener("click", function () {
        var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
        applyTheme(next);
        save(KEYS.theme, next);
      });
    }

    if (box) {
      box.addEventListener("change", function () {
        applySafe(box.checked);
        save(KEYS.safe, box.checked ? "1" : "0");
      });
    }
    if (sel) {
      sel.addEventListener("change", function () {
        applyLang(sel.value);
        save(KEYS.lang, sel.value);
      });
    }

    /* Block empty submits (still POST-only; nothing is sent). */
    var forms = document.querySelectorAll("form");
    for (var f = 0; f < forms.length; f++) {
      forms[f].addEventListener("submit", function (ev) {
        var q = ev.currentTarget.querySelector('input[name="q"]');
        if (q && q.value.trim() === "") {
          ev.preventDefault();
          q.focus();
        }
      });
    }

    /* Close settings when clicking elsewhere. */
    var settings = document.getElementById("settings");
    document.addEventListener("click", function (ev) {
      if (settings && settings.open && !settings.contains(ev.target)) {
        settings.open = false;
      }
    });

    /* Keyboard: "/" focuses search; Escape closes settings, clears, or blurs. */
    document.addEventListener("keydown", function (ev) {
      var input = document.querySelector('input[name="q"]');
      var el = document.activeElement;
      var tag = el && el.tagName ? el.tagName.toLowerCase() : "";
      var typing = tag === "input" || tag === "textarea" || tag === "select" ||
        (el && el.isContentEditable);

      if (ev.key === "/" && !typing && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        if (input) {
          ev.preventDefault();
          input.focus();
          input.select();
        }
      } else if (ev.key === "Escape") {
        if (settings && settings.open) {
          settings.open = false;
          var summary = settings.querySelector("summary");
          if (summary) { summary.focus(); }
        } else if (input && el === input) {
          if (input.value !== "") { input.value = ""; } else { input.blur(); }
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
