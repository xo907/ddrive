/*
 * The source for a shared link's page (DDrive serves it at /f/<share id>, the key follows the "#"):
 * the latest sealed report for that link, opened here in the browser. The key never leaves the browser
 * (browsers don't send what follows "#"), so DDrive passes the report along without being able to
 * read it. Asking for it is also how the phone learns someone is watching, so it sends more often
 * while the page is open, and stops when it's closed.
 */
(function () {
  const FX = (window.FX = window.FX || {});
  const id = (location.pathname.match(/\/f\/([0-9a-f]{32})/) || [])[1] || new URLSearchParams(location.search).get('share');
  const key = location.hash.slice(1);
  let ended = null;

  async function latest() {
    if (!id || !key) { ended = 'This link is incomplete. Ask for it again.'; return null; }
    const res = await fetch('/api/findx/shared?share=' + encodeURIComponent(id), { cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer' });
    if (res.status === 404 || res.status === 410) { ended = 'This link has ended.'; return null; }
    if (!res.ok) throw new Error('DDrive answered ' + res.status);
    const o = await res.json();
    if (!o.blob) return null;
    const r = await FX.openJson(FX.b64.dec(key), 'findx share', FX.b64.dec(o.blob));
    r.until = o.until;
    return r;
  }

  FX.source = {
    caps: { act: false, history: false, pick: false },
    devices: async () => [{ id: 'shared', name: 'Shared location' }],
    latest: async () => {
      try {
        const r = await latest();
        if (ended) FX.ended && FX.ended(ended);
        return r;
      } catch (e) { return FX.lastShared || null; }
    },
    history: async () => [],
    act: async () => ({}),
    watching: async () => ({}),
    initial: async () => ({ id: 'shared' }),
  };
})();
