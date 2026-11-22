// Keyboard layer. Any element with data-key="x" (or a chord, "g b") gets a shortcut;
// data-key-label adds it to the help dialog, so help always matches the current page.
// data-key-alt gives an element a second key, such as an arrow key.
(function () {
  "use strict";

  const CHORD_TIMEOUT_MS = 1200;
  const help = document.getElementById("shortcut-help");
  const chordHint = document.getElementById("chord-hint");
  let pendingChord = null;
  let chordTimer = null;
  let rowIndex = -1;

  const isTyping = (el) =>
    el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

  function bindings() {
    const map = new Map();
    document.querySelectorAll("[data-key]").forEach((el) => {
      if (el.disabled) return;
      if (!map.has(el.dataset.key)) map.set(el.dataset.key, el);
      if (el.dataset.keyAlt && !map.has(el.dataset.keyAlt)) map.set(el.dataset.keyAlt, el);
    });
    return map;
  }

  function activate(el) {
    const details = el.closest("details");
    // A <summary> toggles its own <details> when clicked
    if (details && el.tagName !== "SUMMARY") details.open = true;
    if (el.matches("input, textarea, select")) {
      el.focus();
      if (el.select) el.select();
    } else {
      el.click();
    }
  }

  function moveRow(step) {
    const rows = [...document.querySelectorAll("[data-row]")];
    if (!rows.length) return;
    rowIndex = Math.min(Math.max(rowIndex + step, 0), rows.length - 1);
    const row = rows[rowIndex];
    const link = row.querySelector("[data-row-link]") || row.querySelector("a, button, input");
    if (link) link.focus();
    row.scrollIntoView({ block: "nearest" });
  }

  function setChord(key) {
    clearTimeout(chordTimer);
    pendingChord = key;
    if (chordHint) {
      chordHint.textContent = key ? `${key} …` : "";
      chordHint.hidden = !key;
    }
    if (key) chordTimer = setTimeout(() => setChord(null), CHORD_TIMEOUT_MS);
  }

  function openHelp() {
    const list = help.querySelector("[data-help-list]");
    list.replaceChildren();
    // keys: one key or chord ("g b"), or a list of alternatives
    const add = (keys, label) => {
      const dt = document.createElement("dt");
      [].concat(keys).forEach((key, index) => {
        if (index > 0) dt.append("or ");
        key.split(" ").forEach((part) => {
          const kbd = document.createElement("kbd");
          kbd.textContent = part;
          dt.append(kbd, " ");
        });
      });
      const dd = document.createElement("dd");
      dd.textContent = label;
      list.append(dt, dd);
    };
    const ARROWS = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓" };
    bindings().forEach((el, key) => {
      if (!el.dataset.keyLabel || key !== el.dataset.key) return; // alternates are shown with their element
      const alt = el.dataset.keyAlt;
      add(alt ? [key, ARROWS[alt] || alt] : key, el.dataset.keyLabel);
    });
    if (document.querySelector("[data-row]")) {
      add("j", "Next row");
      add("k", "Previous row");
      if (document.querySelector("a[data-row-link]")) add("Enter", "Open focused row");
    }
    add("Esc", "Leave a field or close this help");
    add("?", "Show this help");
    help.showModal();
  }

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
    if (help && help.open) return; // The dialog handles its own keys.

    const target = event.target;
    if (isTyping(target)) {
      if (event.key === "Escape") {
        event.preventDefault(); // Browsers would otherwise clear type="search" inputs.
        target.blur();
      }
      return;
    }

    if (pendingChord) {
      const el = bindings().get(`${pendingChord} ${event.key}`);
      setChord(null);
      if (el) {
        event.preventDefault();
        activate(el);
      }
      return;
    }

    if (event.key === "g") {
      setChord("g");
    } else if (event.key === "?") {
      event.preventDefault();
      openHelp();
    } else if (event.key === "j" || event.key === "k") {
      event.preventDefault();
      moveRow(event.key === "j" ? 1 : -1);
    } else {
      const el = bindings().get(event.key);
      if (el) {
        event.preventDefault();
        activate(el);
      }
    }
  });

  // Keep j/k in step when focus moves by Tab or mouse.
  document.addEventListener("focusin", (event) => {
    const row = event.target.closest("[data-row]");
    if (row) rowIndex = [...document.querySelectorAll("[data-row]")].indexOf(row);
  });

  document.addEventListener("submit", (event) => {
    const message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) event.preventDefault();
  });

  document.getElementById("shortcut-help-open")?.addEventListener("click", openHelp);
  help?.querySelector("[data-help-close]")?.addEventListener("click", () => help.close());
})();
