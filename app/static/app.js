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
