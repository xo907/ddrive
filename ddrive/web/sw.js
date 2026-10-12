"use strict";
/* For end-to-end encrypted accounts. A picture or a video that the dashboard shows by its address
 * (<img src="/api/file?path=...">) can't come from the server, which only has it sealed. This worker
 * catches those requests and asks the page that made them for the bytes: the page has the keys and
 * opens the pieces that are needed (e2e-api.js). The worker itself has no key and keeps nothing; it
 * only carries the question and the answer. For any other account the page says "not mine" and the
 * request goes to the server as ever. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

function ask(client, message) {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => resolve({ pass: true }), 20000);      // a page that doesn't answer: let the server do it
    channel.port1.onmessage = (e) => { clearTimeout(timer); resolve(e.data || { pass: true }); };
    client.postMessage(message, [channel.port2]);
  });
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !event.clientId) return;
  if (url.pathname !== "/api/file" && url.pathname !== "/api/thumb" && url.pathname !== "/api/chat/preview") return;
  event.respondWith((async () => {
    const client = await self.clients.get(event.clientId);
    if (!client) return fetch(event.request);
    const got = await ask(client, { type: "e2e-get", url: url.href, range: event.request.headers.get("Range") || "" });
    if (got.pass) return fetch(event.request);
    return new Response(got.body || null, { status: got.status || 200, headers: got.headers || {} });
  })());
});
