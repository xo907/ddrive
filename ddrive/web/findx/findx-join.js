"use strict";
/* A circle's invitation (/j/<circle>#<key>): its name is opened here with the key from the link, which
 * the browser never sends; then FindX on the phone is opened with the invitation, to join. */
(function () {
  const $ = (id) => document.getElementById(id);
  const circle = (location.pathname.match(/\/j\/([0-9a-f]{32})/) || [])[1];
  const key = location.hash.slice(1);
  const invite = location.origin + "/j/" + circle + "#" + key;
  function fail(text) { $("name").textContent = "This invitation can't be opened"; $("err").textContent = text; $("err").hidden = false; }
  (async () => {
    if (!circle || !key) return fail("The link is incomplete. Ask for it again.");
    try {
      const k = await FX.circleKeys(FX.b64.dec(key));
      const r = await fetch("/api/findx/c/info", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "omit",
        body: JSON.stringify({ circle, auth: k.auth }) });
      if (r.status === 404) return fail("The circle is gone, or the invitation is out of date.");
      if (!r.ok) return fail("The server answered " + r.status + ".");
      const o = await r.json();
      const info = await FX.openJson(k.seal, "findx circle info", FX.b64.dec(o.info));
      $("name").textContent = info.name || "A circle";
      $("who").textContent = o.members === 1 ? "1 person is in it" : o.members + " people are in it";
      const open = $("open");
      open.href = "findx://join?host=" + encodeURIComponent(location.origin) + "&c=" + circle + "#" + key;
      open.hidden = false;
      const copy = $("copy");
      if (navigator.clipboard) {
        copy.hidden = false;
        copy.onclick = () => navigator.clipboard.writeText(invite).then(() => { copy.textContent = "Copied. In FindX: People › Join with an invitation."; });
      }
    } catch (e) {
      fail("It couldn't be opened: " + e.message);
    }
  })();
})();
