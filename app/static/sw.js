// Minimal service worker: enough for Chrome/Edge to offer "Install app".
// Offline support is out of scope for v0.1, so requests go straight to the network.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
