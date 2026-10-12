"use strict";
/* Moving the owner's drive into an end-to-end encrypted account, from the dashboard.
 *
 * The server may not do this itself: it would need the account's key to seal the files, and the point
 * of such an account is that the server never has it. So this page does it. It makes the account's 24
 * words here (or takes the words of an account there already is), and then goes through the drive file
 * by file: fetched from the server as the owner sees it, sealed here with the account's key, sent back
 * sealed. The page holds two sign-ins for that: the owner's (the cookie) and the account's (a session
 * the server hands this page, sent along with the sealed requests only).
 *
 * It can be stopped and started again: what is in the account already, with the same size and date,
 * is not copied twice. Nothing on the drive is changed or deleted. */
(() => {
  const PIECE = 8 * 1024 * 1024;
  const BY = "moved from the drive";
  let job = null;                            // what is running or was stopped: {vault, stop, done, ...}

  /** A file on the owner's drive, as the sealing code reads a file: a size, and pieces of it on request. */
  function driveFile(it, signal) {
    let at = -1, buf = null;
    return { size: it.size, slice(a, b) {
      return { async arrayBuffer() {
        if (b <= a) return new ArrayBuffer(0);
        const start = Math.floor(a / PIECE) * PIECE;
        if (start !== at || !buf || b > at + buf.length) {
          const end = Math.min(it.size, Math.max(b, start + PIECE)) - 1;
          const r = await fetch("/api/file" + q({ path: it.path }), { credentials: "same-origin", headers: { Range: `bytes=${start}-${end}` }, signal });
          if (!r.ok) throw new Error(`${it.path} could not be read from the drive (${r.status})`);
          buf = new Uint8Array(await r.arrayBuffer());
          if (buf.length !== end - start + 1) throw new Error(`${it.path} changed while it was being copied`);
          at = start;
        }
        return buf.slice(a - at, b - at).buffer;
      } };
    } };
  }

  async function run(state, say) {
    const v = state.vault, ctl = new AbortController();
    state.stop = () => ctl.abort();
    state.running = true;
    const kidsOf = () => { const m = new Map(); for (const n of v.nodes.values()) { if (n.trash) continue; if (!m.has(n.parent)) m.set(n.parent, new Map()); m.get(n.parent).set(n.name, n); } return m; };
    try {
      // what is on the drive
      say({ phase: "Looking at what is on the drive…" });
      const files = [], folders = [];
      const walk = async (path) => {
        for (const it of (await api("/api/list" + q({ path }))).items) {
          if (ctl.signal.aborted) throw new DOMException("Stopped", "AbortError");
          if (it.dir) { folders.push(it); await walk(it.path); } else files.push(it);
        }
      };
      await walk("/");
      await v.sync();
      let have = kidsOf();
      // its folders
      const ids = new Map([["/", ""]]);
      for (const f of folders) {
        const up = ids.get(f.path.slice(0, f.path.lastIndexOf("/")) || "/"), old = (have.get(up) || new Map()).get(f.name);
        if (old && !old.dir) throw new Error(`${f.path} is a folder on the drive and a file in the account. Rename one of them.`);
        if (old) { ids.set(f.path, old.id); continue; }
        const id = DDV.newId();
        await v._send({ op: "put", id, parent: up, name: f.name, dir: true, size: 0, mtime: f.mtime || Date.now() / 1000, at: Date.now() / 1000, by: BY,
                        ...(f.starred ? { star: Date.now() / 1000 } : {}) });
        ids.set(f.path, id);
      }
      have = kidsOf();
      // its files
      const todo = files.filter((f) => {
        const old = (have.get(ids.get(f.path.slice(0, f.path.lastIndexOf("/")) || "/")) || new Map()).get(f.name);
        return !(old && !old.dir && old.size === f.size && Math.abs((old.mtime || 0) - (f.mtime || 0)) < 2);
      });
      const total = todo.reduce((s, f) => s + f.size, 0);
      let bytes = 0, n = 0;
      state.failed = [];
      say({ phase: "", files: files.length, there: files.length - todo.length, todo: todo.length, total, n, bytes });
      for (const f of todo) {
        if (ctl.signal.aborted) throw new DOMException("Stopped", "AbortError");
        say({ file: f.path, n, bytes });
        const parent = ids.get(f.path.slice(0, f.path.lastIndexOf("/")) || "/");
        try {
          const raw = crypto.getRandomValues(new Uint8Array(32)), obj = DDV.newId();
          await v._store(obj, await DDV.fileKey(raw), driveFile(f, ctl.signal), (p) => {
            if (p.bytes !== undefined) say({ bytes: bytes + p.bytes });
            if (p.note !== undefined) say({ note: p.note });
          }, ctl.signal);
          const old = [...v.nodes.values()].find((x) => x.parent === parent && x.name === f.name && !x.trash);
          let entry = { id: DDV.newId(), parent, name: f.name, dir: false, at: Date.now() / 1000, by: BY };
          if (old && !old.dir) {                 // an older copy of it in the account: kept as an earlier version
            entry = { ...old };
            delete entry.thumb;
            entry.versions = [{ obj: old.obj, key: old.key, size: old.size, mtime: old.mtime, at: Date.now() / 1000 }, ...(old.versions || [])].slice(0, 10);
          }
          await v._send({ op: "put", ...entry, size: f.size, mtime: f.mtime || Date.now() / 1000, obj, key: DDV.b64(raw), ...(f.starred ? { star: Date.now() / 1000 } : {}) });
        } catch (e) {
          if (e.name === "AbortError" || e instanceof DDV.Gone) throw e;
          state.failed.push(`${f.path}: ${e.message}`);
        }
        bytes += f.size;
        n += 1;
        say({ n, bytes });
      }
      state.done = true;
      say({ file: "", finished: true, n, failed: state.failed.length });
    } finally {
      state.running = false;
    }
  }

  /** Sign this page in to the account next to the owner, and open the account's list. */
  async function openAccount(key, create) {
    const { auth, meta } = await DDV.derive(key);
    const r = await api("/api/accounts/e2e", { auth, create });
    DDV.as(r.session);
    const vault = new DDV.Vault(meta);
    await vault.sync();
    return { vault, id: r.id, made: r.made };
  }

  window.moveToAccount = async function () {
    if (!DDV.usable) { toast("This needs an https address (or the computer itself): a browser doesn't encrypt on plain http."); return; }
    const body = h("div", { class: "move" });
    const m = modal("Move everything into an end-to-end encrypted account", body);
    m.dlg.addEventListener("close", () => { if (job && job.running) job.stop(); });
    const actions = (...buttons) => h("div", { class: "modal-actions" }, ...buttons);
    const p = (text, cls = "muted") => h("p", { class: cls, style: "margin:0 0 14px" }, text);

    function progress() {
      const head = h("div", { class: "t" }, "Starting…"), sub = h("div", { class: "muted" }, ""), bar = h("i", { style: "width:0%" });
      const stop = h("button", { class: "btn ghost", type: "button", onclick: () => job.stop() }, "Stop");
      const st = { files: 0, there: 0, todo: 0, total: 0, n: 0, bytes: 0, file: "", phase: "", note: "" };
      const say = (change) => {
        Object.assign(st, change);
        if (st.finished) return;
        head.textContent = st.phase || `Copying file ${Math.min(st.n + 1, st.todo)} of ${st.todo}` + (st.there ? ` (${st.there} already in the account)` : "");
        sub.textContent = st.phase ? "" : `${st.note || st.file} · ${size(Math.min(st.bytes, st.total))} of ${size(st.total)}`;
        bar.style.width = `${st.total ? Math.min(100, st.bytes / st.total * 100) : 0}%`;
      };
      body.replaceChildren(p("Each file is fetched from the drive, sealed in this browser with the account's key, and sent back sealed. Keep this tab open; it can take long. "
                             + "Stopping is safe: start again later and it carries on."),
                           head, h("div", { class: "meter" }, bar), sub, actions(stop));
      run(job, say).then(() => {
        const failed = job.failed || [];
        body.replaceChildren(...[
          h("p", { style: "margin:0 0 14px" }, h("b", {}, failed.length ? `${st.n - failed.length} of ${st.todo} files were copied.` : st.todo ? `Done: ${plural(st.todo, "file")} copied.` : "Everything is in the account already."),
            ` The account now holds what the drive holds (${plural(st.files, "file")}).`),
          failed.length ? h("div", {}, p(`${plural(failed.length, "file")} could not be copied. Press “Try those again”.`, "err"),
                            h("pre", { class: "move-log" }, failed.slice(0, 30).join("\n"))) : null,
          p("Nothing on the drive was changed or deleted. To use the account, sign out here and sign in with its 24 words. "
            + "Folders that are hidden or password-locked on this device were not copied unless they were unlocked. Earlier versions, snapshots and share links stay with the drive."),
          actions(failed.length ? h("button", { class: "btn", type: "button", onclick: progress }, "Try those again") : null,
                  h("button", { class: "btn ghost", type: "button", onclick: m.close }, "Close"))].filter(Boolean));
      }, (e) => {
        const stopped = e.name === "AbortError";
        body.replaceChildren(p(stopped ? "Stopped. What was copied so far is in the account." : `It stopped: ${e.message}`, stopped ? "muted" : "err"),
                             actions(h("button", { class: "btn", type: "button", onclick: progress }, "Carry on"),
                                     h("button", { class: "btn ghost", type: "button", onclick: m.close }, "Close")));
      });
    }

    async function begin(key, create, err, button) {
      button.disabled = true;
      try {
        job = { ...(await openAccount(key, create)) };
        progress();
      } catch (e) { err.textContent = e.message; err.hidden = false; button.disabled = false; }
    }

    function existing() {
      const words = h("textarea", { rows: 4, class: "move-words", placeholder: "The account's 24 words", autocomplete: "off", spellcheck: false });
      const err = h("p", { class: "err", hidden: true });
      const go_ = h("button", { class: "btn", type: "button", onclick: async () => {
        let key;
        try { key = await DDV.phraseToKey(words.value); } catch (e) { err.textContent = e.message; err.hidden = false; return; }
        begin(key, false, err, go_);
      } }, "Start copying");
      body.replaceChildren(p("Type the 24 words of the end-to-end encrypted account. They stay in this browser; the server gets a sign-in key made from them."),
                           words, err, actions(go_, h("button", { class: "btn ghost", type: "button", onclick: first }, "Back")));
    }

    async function fresh() {
      const key = DDV.newKey(), phrase = await DDV.keyToPhrase(key);
      const saved = h("input", { type: "checkbox" }), err = h("p", { class: "err", hidden: true });
      const go_ = h("button", { class: "btn", type: "button", disabled: true, onclick: () => begin(key, true, err, go_) }, "Make the account and start copying");
      saved.onchange = () => { go_.disabled = !saved.checked; };
      body.replaceChildren(
        p("These 24 words are the new account: they sign you in and they are the key everything in it is sealed with. They were made in this browser and are shown once. "
          + "Nobody can bring them back, not the server either. Write them down now."),
        h("ol", { class: "move-list" }, phrase.split(" ").map((w) => h("li", {}, w))),
        h("button", { class: "btn ghost small", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(phrase); toast("Copied"); } catch { /* written down instead */ } } }, "Copy"),
        h("label", { class: "move-check" }, saved, " I have written the 24 words down"),
        err, actions(go_, h("button", { class: "btn ghost", type: "button", onclick: first }, "Back")));
    }

    function first() {
      if (job && job.vault && !job.done) {
        body.replaceChildren(p("The copy into the account was stopped."), actions(h("button", { class: "btn", type: "button", onclick: progress }, "Carry on"),
                                                                                  h("button", { class: "btn ghost", type: "button", onclick: () => { job = null; first(); } }, "Another account")));
        return;
      }
      body.replaceChildren(
        p("Your drive's files can be read by the computer that runs it: it holds the drive's key. An end-to-end encrypted account's can't: they are sealed on your own devices with 24 words that never leave them."),
        p("This copies everything on the drive into such an account: folders, files with their dates, stars, and your notes, contacts, bookmarks and saved messages, which the account's pages then show. "
          + "It is done in this browser, so the server never gets the account's key. Nothing on the drive is changed."),
        actions(h("button", { class: "btn", type: "button", onclick: fresh }, "Make a new account"),
                h("button", { class: "btn ghost", type: "button", onclick: existing }, "Use an account I have")));
    }
    first();
  };
})();
