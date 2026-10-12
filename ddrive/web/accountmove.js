"use strict";
/* Moving an end-to-end encrypted account into another one (ddrive/transfer.py tells the whole story).
 *
 * Two pages take part, each signed in to its own account: the one that receives (T) and the one that
 * moves out (S). They agree on a key with ECDH (P-256) through the server, which sees only the two
 * public keys. S seals its whole list of files with that key; the server copies S's sealed files to T
 * without opening them and passes the sealed list on; T opens it and adds it to its own list in a new
 * folder. Then S is deleted.
 *
 * The person compares two codes between the two screens: the code of T's key before S sends (so the
 * server can't slip in a key of its own and read the list, with every file's key in it) and the code of
 * both keys before T takes the list in (so the server can't slip in a list of its own).
 *
 * T's private key is kept in this browser (IndexedDB, as a key that can be used but not read out) until
 * the move is done, so the page may be closed while the server copies. */
(() => {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const LABEL = enc.encode("ddv1 move"), INFO = enc.encode("ddrive move v1 key");
  const BATCH = 2 * 1024 * 1024;
  const ECDH = { name: "ECDH", namedCurve: "P-256" };
  const cat = (...parts) => { const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let at = 0; for (const p of parts) { out.set(p, at); at += p.length; } return out; };
  const sha = async (...parts) => new Uint8Array(await crypto.subtle.digest("SHA-256", cat(...parts)));
  const hexOf = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function base32(bytes) {
    const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    let bits = 0, value = 0, out = "";
    for (const b of bytes) { value = (value << 8) | b; bits += 8; while (bits >= 5) { out += A[(value >>> (bits - 5)) & 31]; bits -= 5; } }
    if (bits > 0) out += A[(value << (5 - bits)) & 31];
    return out;
  }
  const code = async (...parts) => { const s = base32((await sha(...parts)).subarray(0, 5)); return `${s.slice(0, 4)}-${s.slice(4)}`; };
  const keyCode = (tpub) => code(enc.encode("ddrive move v1 code"), tpub);
  const checkCode = (tpub, spub) => code(enc.encode("ddrive move v1 check"), tpub, spub);

  async function agree(priv, theirs, tpub, spub) {
    const pub = await crypto.subtle.importKey("raw", theirs, ECDH, false, []);
    const shared = await crypto.subtle.deriveBits({ name: "ECDH", public: pub }, priv, 256);
    const base = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: await sha(tpub, spub), info: INFO }, base,
                                   { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function keypair() {
    const kp = await crypto.subtle.generateKey(ECDH, false, ["deriveBits"]);
    return { priv: kp.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey)) };
  }
  const movedId = async (mid, old) => hexOf(await sha(enc.encode("ddrive move v1 id" + mid + old))).slice(0, 32);
  const movedFolder = async (mid) => hexOf(await sha(enc.encode("ddrive move v1 folder" + mid))).slice(0, 32);

  // ------------------------------------------------------------ T's pending move, kept in this browser
  function db() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("ddrive-move", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("moves");
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function kept(mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("moves", mode), req = fn(tx.objectStore("moves"));
      tx.oncomplete = () => { d.close(); resolve(req && req.result); };
      tx.onerror = tx.onabort = () => { d.close(); reject(tx.error); };
    });
  }
  const mine = () => (window.E2E && E2E.status && E2E.status.account) || "";
  const pending = {
    get: () => kept("readonly", (s) => s.get(mine())),
    put: (m) => kept("readwrite", (s) => s.put(m, mine())),
    drop: () => kept("readwrite", (s) => s.delete(mine())),
  };

  const vaultOf = async () => { await E2E.ready; if (!E2E.vault) throw new Error("This account's files aren't open in this browser."); return E2E.vault; };
  const status = (id) => DDV.call(`/api/vault/move?id=${encodeURIComponent(id)}`);
  const P = (text, cls = "muted") => h("p", { class: cls, style: "margin:0 0 14px" }, text);
  const big = (text) => h("div", { class: "move-code", style: "font:600 1.6em/1.3 ui-monospace,monospace;letter-spacing:.08em;margin:6px 0 16px" }, text);
  const actions = (...b) => h("div", { class: "modal-actions" }, ...b);
  const usable = () => { if (!DDV.usable || !crypto.subtle) { toast("This needs an https address (or the computer itself): a browser doesn't encrypt on plain http."); return false; } return true; };

  /** What T does with what came: its whole list, into a new folder here. */
  async function takeIn(m, st) {
    const v = await vaultOf();
    const spub = DDV.unb64(st.spub);
    const sealed = await DDV.call(`/api/vault/move/list?id=${m.id}`, { raw: true });
    const key = await agree(m.priv, spub, m.tpub, spub);
    const data = JSON.parse(dec.decode(await DDV.unseal(key, sealed, LABEL)));
    await v.sync();
    const top = await movedFolder(m.id);
    const name = v.nodes.has(top) ? v.nodes.get(top).name : v.freeName("", `From account ${st.from}`);
    const now = Date.now() / 1000;
    const ops = [{ op: "put", id: top, parent: "", name, dir: true, size: 0, mtime: now, at: now, by: "moved in" }];
    for (const [old, n] of Object.entries(data.nodes || {})) {
      const e = { ...n, id: await movedId(m.id, old), parent: n.parent ? await movedId(m.id, n.parent) : top };
      if (e.trash && e.trash.path) e.trash = { ...e.trash, path: "/" + name + "/" + String(e.trash.path).replace(/^\/+/, "") };
      ops.push({ ...e, op: "put" });
    }
    let batch = [], bytes = 0;
    for (const op of ops) {
      const n = JSON.stringify(op).length;
      if (batch.length && bytes + n > BATCH) { await v._send(...batch); batch = []; bytes = 0; }
      batch.push(op); bytes += n;
    }
    if (batch.length) await v._send(...batch);
    await DDV.call("/api/vault/move/finish", { method: "POST", json: { id: m.id } });
    await pending.drop().catch(() => null);
    return name;
  }

  // ------------------------------------------------------------ T: receive
  /** `cur`: the move this account is in already, as its status says (the card that follows a move opens this). */
  async function receive(cur) {
    if (!usable()) return;
    const body = h("div", { class: "move" });
    const dlg = modal("Move another account into this one", body);
    let alive = true;
    dlg.dlg.addEventListener("close", () => { alive = false; });
    let m = await pending.get().catch(() => null);
    if (m) {
      try { await status(m.id); } catch (e) { await pending.drop().catch(() => null); m = null; }
    }
    // Started in another browser, which has the key for it: here it can be followed and cancelled.
    if (cur && cur.side === "to" && (!m || m.id !== cur.id)) m = { id: cur.id, remote: true };
    if (!m) {
      body.replaceChildren(P("Everything in another end-to-end encrypted account of yours — files, folders, earlier versions, deleted files — comes into a new folder here. "
                             + "The other account is deleted afterwards. The server copies the sealed files and can open none of them."),
                           P("You need the other account open on another screen (another browser or device, signed in with its own 24 words)."),
                           actions(h("button", { class: "btn", type: "button", onclick: start }, "Start")));
      return;
    }
    follow(m);

    async function start() {
      const { priv, pub } = await keypair();
      const got = await attempt(() => DDV.call("/api/vault/move/open", { method: "POST", json: { pub: DDV.b64(pub) } }));
      if (!got) return;
      m = { id: got.id, priv, tpub: pub, at: Date.now() };
      await pending.put(m);
      follow(m);
    }

    async function follow(m) {
      const mine = m.tpub ? await keyCode(m.tpub) : "";
      while (alive) {
        let st;
        try { st = await status(m.id); } catch (e) {
          body.replaceChildren(P(e.message, "err"), actions(h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")));
          if (!m.remote) await pending.drop().catch(() => null);
          return;
        }
        const mycode = mine || st.code;
        const cancel = h("button", { class: "btn ghost", type: "button", onclick: async () => {
          if (await attempt(() => DDV.call("/api/vault/move/cancel", { method: "POST", json: { id: m.id } }), "The move was cancelled")) { if (!m.remote) await pending.drop().catch(() => null); dlg.close(); }
        } }, "Cancel the move");
        if (st.state === "open") {
          body.replaceChildren(P("On the account that moves out, open Settings → “Move everything into another account” and type this:"),
                               big(m.id.toUpperCase().replace(/(.{4})(?=.)/g, "$1 ")),
                               P("It will show a code. It has to be this one; if it isn't, don't go on there:"), big(mycode),
                               P(`Waiting for the other account… (this request runs out ${st.expires ? "at " + new Date(st.expires * 1000).toLocaleTimeString() : "in 30 minutes"})`),
                               actions(cancel));
        } else if (st.state === "copying") {
          const pct = st.bytes ? Math.min(100, (st.done_bytes || 0) / st.bytes * 100) : 0;
          body.replaceChildren(P("The other account sent its list. The server is copying its sealed files into this account; you can close this window and come back."),
                               P(`Check that the other page shows ${st.check}.`),
                               h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })),
                               P(`${st.copied || 0} of ${st.objects || "?"} pieces · ${size(st.done_bytes || 0)} of ${size(st.bytes || 0)}`), actions(cancel));
        } else if (st.state === "ready" && m.remote) {
          body.replaceChildren(P("Everything is copied. It is taken in from the browser the move was started in, which keeps the key for it: open this account there and choose Your account → “Receive…”."),
                               P(`The other page shows the code ${st.check}.`), actions(cancel));
        } else if (st.state === "ready") {
          const ok = h("button", { class: "btn", type: "button", onclick: async () => {
            ok.disabled = true;
            body.replaceChildren(P("Taking it in…"));
            try {
              const name = await takeIn(m, st);
              body.replaceChildren(P(h("b", {}, `Done: everything is in the folder “${name}”.`)),
                                   P("The other account is deleted as soon as the server has stored the files on Discord as this account's."),
                                   actions(h("a", { class: "btn", href: "#/files/" + encodeURIComponent(name), onclick: () => dlg.close() }, "Open it")));
            } catch (e) {
              body.replaceChildren(P(e.message || String(e), "err"), actions(h("button", { class: "btn", type: "button", onclick: () => follow(m) }, "Try again")));
            }
          } }, "They match: take it in");
          body.replaceChildren(P("Everything is copied. Last check: the other page shows a code too. It has to be this one:"), big(st.check),
                               P("If it is different, something other than your account sent this: cancel."), actions(cancel, ok));
          return;
        } else if (st.state === "finishing" || st.state === "done") {
          if (!m.remote) await pending.drop().catch(() => null);
          body.replaceChildren(P(h("b", {}, "This move is done.")), actions(h("button", { class: "btn", type: "button", onclick: () => dlg.close() }, "Close")));
          return;
        } else {
          if (!m.remote) await pending.drop().catch(() => null);
          body.replaceChildren(P(`The move was ${st.state}${st.error ? ": " + st.error : "."}`, "err"),
                               actions(h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")));
          return;
        }
        await sleep(2000);
      }
    }
  }

  // ------------------------------------------------------------ S: move out
  /** `cur`: the move this account is in already (the card that follows a move opens this): straight to how it is going. */
  async function send(cur) {
    if (!usable()) return;
    const body = h("div", { class: "move" });
    const dlg = modal("Move everything into another account", body);
    let alive = true;
    dlg.dlg.addEventListener("close", () => { alive = false; });
    if (cur && cur.side === "from") { body.replaceChildren(P("Looking at how the move is going…")); watch(cur.id, cur.check); return; }
    const input = h("input", { placeholder: "The code the other account shows", spellcheck: "false", autocomplete: "off", style: "font-family:ui-monospace,monospace" });
    const look = h("button", { class: "btn", type: "button", onclick: lookUp }, "Next");
    body.replaceChildren(P("Everything in this account — files, folders, earlier versions, deleted files — moves into another end-to-end encrypted account, into a new folder there. "
                           + "When it is done, this account is deleted and its 24 words stop working. Share links of this account stop working too."),
                         P("On the other account, open Settings → “Move another account into this one” and press Start. Type the code it shows:"),
                         field("Code", input), actions(look));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") lookUp(); });
    input.focus();

    async function lookUp() {
      const id = input.value.toLowerCase().replace(/[^a-z2-7]/g, "");
      const peer = await attempt(() => DDV.call(`/api/vault/move/peer?id=${id}`));
      if (!peer) return;
      const tpub = DDV.unb64(peer.tpub);
      const theirs = await keyCode(tpub);
      const go_ = h("button", { class: "btn danger", type: "button", onclick: () => go(peer, tpub) }, "The same: move everything");
      body.replaceChildren(P("The other account's page shows a code. It has to be exactly this one:"), big(theirs),
                           P("If it is different, the server gave this page a key that isn't the other account's: stop here."),
                           P(`This account then moves into account ${peer.to} and is deleted. It can't change while the move runs.`),
                           actions(h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Stop"), go_));
    }

    async function go(peer, tpub) {
      body.replaceChildren(P("Sealing the list of files…"));
      try {
        const v = await vaultOf();
        await v.sync();
        const { priv, pub } = await keypair();
        const key = await agree(priv, tpub, tpub, pub);
        const sealed = await DDV.seal(key, enc.encode(JSON.stringify({ v: 1, nodes: Object.fromEntries(v.nodes) })), LABEL);
        const got = await DDV.call(`/api/vault/move/send?id=${peer.id}&pub=${encodeURIComponent(DDV.b64(pub))}`,
                                   { method: "POST", body: sealed, headers: { "Content-Type": "application/octet-stream" } });
        if (got.check !== await checkCode(tpub, pub)) throw new Error("The server's check code isn't the one these keys make. Cancel the move on the other account.");
        watch(peer.id, got.check);
      } catch (e) {
        body.replaceChildren(P(e.message || String(e), "err"), actions(h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")));
      }
    }

    async function watch(id, check) {
      while (alive) {
        let st;
        try { st = await status(id); } catch (e) {
          if (e instanceof DDV.Gone || e.status === 401) {
            body.replaceChildren(P(h("b", {}, "Done: this account was moved and deleted.")), P("Its 24 words no longer sign in. Everything is in the other account now."),
                                 actions(h("button", { class: "btn", type: "button", onclick: () => location.reload() }, "OK")));
            return;
          }
          body.replaceChildren(P(e.message, "err"));
          return;
        }
        const pct = st.bytes ? Math.min(100, (st.done_bytes || 0) / st.bytes * 100) : 0;
        if (st.state === "cancelled" || st.state === "failed") {
          body.replaceChildren(P(`The move was ${st.state}${st.error ? ": " + st.error : "."} Nothing in this account changed.`, "err"),
                               actions(h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Close")));
          return;
        }
        if (st.state === "finishing" || st.state === "done") {
          body.replaceChildren(P(h("b", {}, "Done: everything is in the other account now.")),
                               P("This account is deleted as soon as the server has stored the files as the other account's; this page then signs out. Its 24 words no longer sign in after that."));
          await sleep(5000);
          continue;
        }
        body.replaceChildren(P("Sent. Check that the other account's page shows this code before it takes everything in:"), big(check),
                             st.state === "copying" ? h("div", {}, h("div", { class: "meter" }, h("i", { style: `width:${pct}%` })),
                                                       P(`The server copies the sealed files: ${st.copied || 0} of ${st.objects || "?"} · ${size(st.done_bytes || 0)} of ${size(st.bytes || 0)}`))
                               : P(st.state === "ready" ? "Everything is copied. Waiting for the other account to take it in." : "Taken in. This account is deleted once the files are stored."),
                             actions(st.state === "copying" || st.state === "ready"
                               ? h("button", { class: "btn ghost", type: "button", onclick: async () => {
                                   if (await attempt(() => DDV.call("/api/vault/move/cancel", { method: "POST", json: { id } }), "The move was cancelled")) dlg.close();
                                 } }, "Cancel the move") : null));
        await sleep(2000);
      }
    }
  }

  /** Show how the move this account is in is going (`move`: what its status says of it). */
  const show = (move) => (move && move.side === "from" ? send(move) : receive(move));

  window.accountMove = { receive, send, show, pending: () => pending.get().catch(() => null),
                         crypto: { keyCode, checkCode, agree, keypair, movedId, movedFolder } };      // (checked against vault.py in the tests)
})();
