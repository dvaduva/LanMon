// Afișează datele (salvate în UTC) în ora locală a telefonului.
function localizeTimes(root) {
  const fmt = new Intl.DateTimeFormat("ro-RO", { dateStyle: "short", timeStyle: "short" });
  for (const el of (root || document).querySelectorAll("time[data-local]")) {
    const d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) el.textContent = fmt.format(d);
  }
}
document.addEventListener("DOMContentLoaded", () => localizeTimes());
document.addEventListener("htmx:afterSwap", (e) => localizeTimes(e.target));

// Camere colapsate pe Dashboard: starea se ține în browser și se reaplică după fiecare
// reîmprospătare HTMX (fragmentul vine mereu cu toate camerele deschise).
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

// Actualizare live: evenimentele SSE declanșează reîmprospătarea fragmentelor HTMX
// (elementele cu hx-trigger="lanmon:changed from:body").
document.addEventListener("DOMContentLoaded", () => {
  if (!document.body.hasAttribute("data-live")) return;
  if (!window.EventSource) return;
  let timer = null;
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(() => htmx.trigger(document.body, "lanmon:changed"), 300);
  };
  const source = new EventSource("/events");
  for (const kind of ["device_status", "wol", "scan_completed", "device_discovered"]) {
    source.addEventListener(kind, changed);
  }
});

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
