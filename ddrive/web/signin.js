"use strict";
/* The sign-in pages: copying a new account's words, and signing in with a passkey. */
(function () {
  var copy = document.getElementById("copy"), list = document.getElementById("words");
  if (!copy || !list || !navigator.clipboard) return;
  copy.hidden = false;
  copy.addEventListener("click", function () {
    var w = []; list.querySelectorAll("li").forEach(function (li) { w.push(li.textContent); });
    navigator.clipboard.writeText(w.join(" ")).then(function () { copy.textContent = "Copied. Paste them into a password manager."; }, function () {});
  });
})();
(function () {
  var box = document.getElementById("pk"), go = document.getElementById("pk-go"), err = document.getElementById("pk-err");
  if (!box || !window.PublicKeyCredential || !navigator.credentials) {
    var d = document.querySelector("details"); if (d) d.open = true;      // no passkeys in this browser: show the other ways
    return;
  }
  box.hidden = false;
  function bytes(s) { s = s.replace(/-/g, "+").replace(/_/g, "/"); var b = atob(s + "===".slice((s.length + 3) % 4)); var a = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; }
  function text(buf) { var a = new Uint8Array(buf), s = ""; for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
  function post(url, body) { return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-DD": "1" }, body: JSON.stringify(body || {}) }).then(function (r) { return r.text().then(function (t) { var j; try { j = JSON.parse(t); } catch (e) { throw new Error("The drive gave an unexpected answer (" + r.status + "). Reload the page and try again."); } if (!r.ok) throw new Error(j.error || "Sign-in failed"); return j; }); }); }
  function fail(m) { err.textContent = m; err.hidden = false; go.disabled = false; }
  go.addEventListener("click", function () {
    go.disabled = true; err.hidden = true;
    post("/passkey/options").then(function (o) {
      return navigator.credentials.get({ publicKey: { challenge: bytes(o.challenge), rpId: o.rpId, userVerification: "preferred", timeout: 120000,
        allowCredentials: o.allow.map(function (id) { return { type: "public-key", id: bytes(id) }; }) } });
    }).then(function (c) {
      return post("/passkey/login", { id: text(c.rawId), clientDataJSON: text(c.response.clientDataJSON),
                                      authenticatorData: text(c.response.authenticatorData), signature: text(c.response.signature) });
    }).then(function () { location.replace("/"); }).catch(function (e) {
      fail(e && e.name === "NotAllowedError" ? "Cancelled, or no passkey for this address on this device. Use another way below." : (e.message || "Sign-in failed"));
    });
  });
})();
