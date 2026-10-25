// Service worker minimal: face aplicația instalabilă; nu ține nimic în cache,
// pentru că starea dispozitivelor trebuie să fie mereu cea reală.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
