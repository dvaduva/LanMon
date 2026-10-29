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

// Dashboard: list or tiles, the choice is kept in the browser.
const VIEW_KEY = "lanmon:view";
function applyView(view) {
  if (view === "tiles") document.documentElement.dataset.view = "tiles";
  else delete document.documentElement.dataset.view;
  for (const b of document.querySelectorAll("[data-view-toggle]")) {
    b.setAttribute("aria-pressed", String(b.dataset.viewToggle === (view || "list")));
  }
}
document.addEventListener("DOMContentLoaded", () => {
  let view = null;
  try {
    view = localStorage.getItem(VIEW_KEY);
  } catch {}
  applyView(view);
});
document.addEventListener("click", (e) => {
  const button = e.target.closest && e.target.closest("[data-view-toggle]");
  if (!button) return;
  applyView(button.dataset.viewToggle);
  try {
    localStorage.setItem(VIEW_KEY, button.dataset.viewToggle);
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
