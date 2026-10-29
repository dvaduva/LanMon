// Shows dates (stored in UTC) in the device's local time, in the page's language.
function localizeTimes(root) {
  const lang = document.documentElement.lang || undefined;
  const fmt = new Intl.DateTimeFormat(lang, { dateStyle: "short", timeStyle: "short" });
  for (const el of (root || document).querySelectorAll("time[data-local]")) {
    const d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) el.textContent = fmt.format(d);
  }
}
document.addEventListener("DOMContentLoaded", () => localizeTimes());
document.addEventListener("htmx:afterSwap", (e) => localizeTimes(e.target));

// Collapsed rooms on the Dashboard: kept in the browser and reapplied after every
// HTMX refresh (the fragment always arrives with all rooms open).
const COLLAPSED_KEY = "lanmon:collapsed-rooms";
function collapsedRooms() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
function applyCollapsed(root) {
  const collapsed = collapsedRooms();
  for (const el of (root || document).querySelectorAll("details.room[data-room]")) {
    el.open = !collapsed.has(el.dataset.room);
  }
}
document.addEventListener("DOMContentLoaded", () => applyCollapsed());
document.addEventListener("htmx:afterSwap", () => applyCollapsed());
document.addEventListener("toggle", (e) => {
  const el = e.target;
  if (!el.matches || !el.matches("details.room[data-room]")) return;
  const collapsed = collapsedRooms();
  if (el.open) collapsed.delete(el.dataset.room);
  else collapsed.add(el.dataset.room);
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  } catch {}
}, true);

// Folding cards (Settings): <details data-fold="page:card">, the folded ones are kept in the browser.
const FOLDED_KEY = "lanmon:folded";
function foldedCards() {
  try {
    return new Set(JSON.parse(localStorage.getItem(FOLDED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}
document.addEventListener("DOMContentLoaded", () => {
  const folded = foldedCards();
  for (const el of document.querySelectorAll("details[data-fold]")) {
    // A card opened through its anchor (e.g. #api after creating a token) stays open.
    el.open = !folded.has(el.dataset.fold) || location.hash === "#" + el.id;
  }
});
document.addEventListener("toggle", (e) => {
  const el = e.target;
  if (!el.matches || !el.matches("details[data-fold]")) return;
  const folded = foldedCards();
  if (el.open) folded.delete(el.dataset.fold);
  else folded.add(el.dataset.fold);
  try {
    localStorage.setItem(FOLDED_KEY, JSON.stringify([...folded]));
  } catch {}
}, true);

// List or tiles: the choice is kept in the browser, per page (data-view-key).
function applyView(view) {
  document.documentElement.dataset.view = view;
  for (const b of document.querySelectorAll("[data-view-toggle]")) {
    b.setAttribute("aria-pressed", String(b.dataset.viewToggle === view));
  }
}
document.addEventListener("DOMContentLoaded", () => {
  const group = document.querySelector("[data-view-key]");
  if (!group) return;
  let view = null;
  try {
    view = localStorage.getItem(group.dataset.viewKey);
  } catch {}
  applyView(view === "tiles" || view === "list" ? view : group.dataset.viewDefault);
});
document.addEventListener("click", (e) => {
  const button = e.target.closest && e.target.closest("[data-view-toggle]");
  if (!button) return;
  applyView(button.dataset.viewToggle);
  try {
    localStorage.setItem(button.closest("[data-view-key]").dataset.viewKey, button.dataset.viewToggle);
  } catch {}
});

// Forms with data-confirm ask before submitting (the text is translated server-side).
document.addEventListener("submit", (e) => {
  const message = e.target.dataset && e.target.dataset.confirm;
  if (message && !window.confirm(message)) e.preventDefault();
});

// Live updates: SSE events refresh the HTMX fragments
// (elements with hx-trigger="lanmon:changed from:body").
document.addEventListener("DOMContentLoaded", () => {
  if (!document.body.hasAttribute("data-live")) return;
  if (!window.EventSource) return;
  let timer = null;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(() => htmx.trigger(document.body, "lanmon:changed"), 300);
  };
  const source = new EventSource("/events");
  for (const kind of ["device_status", "service_status", "wol", "scan_completed", "device_discovered"]) {
    source.addEventListener(kind, changed);
  }
});

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
