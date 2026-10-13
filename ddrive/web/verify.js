"use strict";
/* "Don't trust, verify": what happens to a file, shown with the person's own files, in their own browser.
 *
 * Nothing on this page is a picture of how it works: every value on it is made here, now, by the same
 * code that seals and opens the files of an end-to-end encrypted account (vaultcore.js), and the checks
 * ask the server for what it really holds. Nothing the page is given to look at is stored or sent,
 * unless it says so. It also says what it can't show, and what still has to be trusted. */
(() => {
  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const spaced = (b, max = 40) => Array.from(b.subarray(0, max), (x) => x.toString(16).padStart(2, "0")).join(" ") + (b.length > max ? " …" : "");
  const readable = (b, max = 64) => Array.from(b.subarray(0, max), (x) => (x >= 32 && x < 127 ? String.fromCharCode(x) : "·")).join("") + (b.length > max ? "…" : "");
  const sha256 = async (b) => hex(new Uint8Array(await crypto.subtle.digest("SHA-256", b)));
  const REPO = "https://github.com/xo907/ddrive";
  const SCRIPTS = ["vaultcore.js", "e2e-api.js", "app.js", "verify.js", "e2e.js", "sw.js", "moving.js", "accountmove.js"];

  const P = (...text) => h("p", { class: "vf-p" }, ...text);
  const small = (...text) => h("p", { class: "vf-p muted small" }, ...text);
  const bytes = (label, b, note, max) => h("div", { class: "vf-bytes" }, h("div", { class: "vf-label" }, label, note && h("span", { class: "muted" }, " · " + note)),
    h("code", {}, b.length ? spaced(b, max) : "(nothing: the file is empty)"));
  const value = (label, text, note) => h("div", { class: "vf-bytes" }, h("div", { class: "vf-label" }, label, note && h("span", { class: "muted" }, " · " + note)), h("code", {}, text));
  const step = (n, title, ...body) => h("section", { class: "vf-step" }, h("div", { class: "vf-n" }, String(n)), h("div", { class: "vf-body" }, h("h3", {}, title), ...body));
  const verdict = (ok, text) => h("div", { class: "vf-verdict " + (ok ? "ok" : "no") }, icon(ok ? "check" : "close"), h("span", {}, text));
  const section = (title, ...body) => [h("h2", {}, title), ...body];

  // ------------------------------------------------------------ 1: a file of yours, locked here
  async function lockDemo(file, into) {
    into.replaceChildren(P("Working on it…"));
    const count = DDV.chunksOf(file.size);
    const first = new Uint8Array(await file.slice(0, DDV.CHUNK).arrayBuffer());
    const raw = crypto.getRandomValues(new Uint8Array(32));              // a key for this demonstration only
    const key = await DDV.fileKey(raw);
    const label = DDV.chunkLabel(0, count === 1);
    const sealed = await DDV.seal(key, first, label);
    const id = DDV.newId();
    const print = await sha256(first);
    const tries = h("div", { class: "vf-tries" });
    const say = (el) => tries.replaceChildren(el);
    const open = async (k, blob) => { try { return await DDV.unseal(k, blob, label); } catch (e) { return null; } };

    into.replaceChildren(
      step(1, "Your file, as it is on this device",
        P(h("b", {}, file.name), ` · ${size(file.size)}. This is how it starts. Anybody who has these bytes has your file:`),
        bytes("The first bytes", first), value("The same, read as text", readable(first) || "(empty)"),
        value("Its fingerprint (SHA-256 of the first piece)", print, "two different things never have the same one")),
      step(2, "A key is made, for this file alone",
        P("Your browser rolls dice: 256 random yes-or-no answers. That is the key. There are more possible keys than atoms in the Earth, many times over, so nobody finds one by trying."),
        bytes("This file's key", raw, "made here a moment ago, only for this demonstration, and thrown away when you leave", 32),
        small("In your account every file gets a key of its own like this one. The keys are kept in your list of files, which is itself locked with a key made from your 24 words.")),
      step(3, "The file is cut into pieces",
        P(`Pieces of 1 MiB, so a video can be played from the middle without fetching all of it. This file is ${plural(count, "piece")}. Each piece is locked on its own, and each lock knows which piece it is: `
          + "pieces can't be swapped, left out or cut off without the lock noticing.")),
      step(4, "Each piece is locked (AES-256-GCM)",
        P("This is the first piece after locking. It is the same length as before plus 28 bytes: a number used once (12 bytes), a seal (16 bytes), and then your bytes, scrambled."),
        bytes("Number used once", sealed.subarray(0, 12), "random, different every time, not secret"),
        bytes("The seal", sealed.subarray(12, 28), "changes completely if a single bit of the piece is changed"),
        bytes("Your bytes, locked", sealed.subarray(28)),
        value("The same, read as text", readable(sealed.subarray(28)) || "(empty)", "compare with step 1"),
        small("AES-256-GCM is a public standard, used by banks, governments and most https websites. It isn't ours, and it has been attacked in the open by researchers for over twenty years. There is no known way to open it without the key.")),
      step(5, "This is all the server would be given",
        value("The name it is stored under", id, "random; it says nothing about the file"),
        value("How much", `${size(DDV.HEAD + file.size + DDV.OVERHEAD * count)} of locked bytes`),
        P("Not given: the file's name, its folder, what kind of file it is, its date, the key. Your real name for it is in your locked list, which the server can't read either."),
        small("In this demonstration nothing was sent at all: the file never left this page.")),
      step(6, "Now try to get in",
        P("Don't take our word for the lock. Try it:"),
        h("div", { class: "vf-actions" },
          h("button", { class: "btn small", type: "button", onclick: async () => {
            const out = await open(key, sealed);
            const same = out && await sha256(out) === print;
            say(verdict(!!same, same ? `Opened with the right key. The fingerprint is ${print.slice(0, 16)}…: exactly the one from step 1, so it is your file again, bit for bit.` : "It did not open: that should not happen. Reload the page and try again."));
          } }, "Open it with the right key"),
          h("button", { class: "btn ghost small", type: "button", onclick: async () => {
            const wrong = crypto.getRandomValues(new Uint8Array(32));
            const out = await open(await DDV.fileKey(wrong), sealed);
            say(verdict(out === null, out === null ? `A new random key (${hex(wrong).slice(0, 16)}…) was tried. The lock refused: without the right key there is nothing to read, not even a part.` : "It opened with a wrong key: that should never happen."));
          } }, "Try another key"),
          h("button", { class: "btn ghost small", type: "button", onclick: async () => {
            const changed = sealed.slice(), at = Math.floor(Math.random() * changed.length), bit = 1 << Math.floor(Math.random() * 8);
            changed[at] ^= bit;
            const out = await open(key, changed);
            say(verdict(out === null, out === null ? `One single bit was flipped (byte ${at + 1} of ${changed.length}), and the right key was used. The lock refused: it notices any change, so nobody can alter your file on the way without you finding out.`
                                                   : "It opened although it was changed: that should never happen."));
          } }, "Change one bit, then open it")),
        tries));
  }

  function lockSection() {
    const out = h("div", {});
    const pick = h("input", { type: "file", hidden: true, onchange: () => { if (pick.files[0]) lockDemo(pick.files[0], out).catch((e) => out.replaceChildren(h("p", { class: "err" }, e.message))); pick.value = ""; } });
    return section("Watch one of your files being locked",
      P("Choose any file. Everything below happens in this page, with the same code that locks the files of an end-to-end encrypted account. ", h("b", {}, "The file is not uploaded and not stored"), ": it is only looked at here."),
      h("div", { class: "vf-actions" }, h("button", { class: "btn", type: "button", onclick: () => pick.click() }, "Choose a file"), pick), out);
  }

  // ------------------------------------------------------------ 2: a file that is really stored
  async function storedCheck(node, into) {
    into.replaceChildren(P("Asking the server for what it has…"));
    const v = E2E.vault, count = DDV.chunksOf(node.size);
    const want = DDV.HEAD + Math.min(node.size, DDV.CHUNK) + DDV.OVERHEAD;
    const got = await DDV.call(`/api/vault/object?id=${node.obj}`, { raw: true, headers: { Range: `bytes=0-${want - 1}` } });
    const piece = got.subarray(DDV.HEAD);
    let plain = null;
    try { plain = await DDV.unseal(await DDV.fileKey(DDV.unb64(node.key)), piece, DDV.chunkLabel(0, count === 1)); } catch (e) { plain = null; }
    const tampered = piece.slice();
    if (tampered.length) tampered[tampered.length - 1] ^= 1;
    let refused = false;
    try { await DDV.unseal(await DDV.fileKey(DDV.unb64(node.key)), tampered, DDV.chunkLabel(0, count === 1)); } catch (e) { refused = true; }
    into.replaceChildren(
      step(1, "What the server has of it",
        P("This was fetched from the server just now. It is everything it holds for this file:"),
        value("The name the server knows it by", node.obj, "it has never seen the real name"),
        bytes("How the server's copy starts", got, `“DDV1” and a size marker, then locked bytes · ${size(DDV.HEAD + node.size + DDV.OVERHEAD * count)} in all`),
        value("The same, read as text", readable(got))),
      step(2, "What only this browser knows",
        value("Its real name and place", "/" + v.pathOf(node.id).join("/")),
        value("Its key", "kept in your locked list of files; not shown here, so that it isn't on your screen"),
        plain ? bytes("The first piece, opened here with that key", plain) : h("p", { class: "err" }, "It could not be opened here."),
        plain ? value("Read as text", readable(plain) || "(empty)") : null),
      step(3, "Checked",
        verdict(!!plain, plain ? "The server's copy opens with the key from your list: it is your file, and the server holds it locked." : "The server's copy did not open with the key from your list."),
        verdict(refused, refused ? "With one bit of the server's copy changed, the lock refused to open it: the server (or anybody between you and it) can't alter your file unnoticed."
                                 : "A changed copy opened: that should never happen.")));
  }

  async function listCheck(into) {
    const v = E2E.vault;
    const [log, account] = await Promise.all([DDV.call("/api/vault/log?after=0"), DDV.call("/api/account")]);
    const batch = (log.batches || [])[0];
    const names = [...v.nodes.values()].filter((n) => !n.trash).length;
    into.replaceChildren(
      P(`Your list has ${plural(names, "file and folder", "files and folders")}: every name, every folder, every size and date, and every file's key. This is that list as the server stores it`
        + (batch ? ", in locked changes, of which this is the first:" : ", locked as one piece:")),
      batch ? bytes("A change to your list, as the server has it", DDV.unb64(batch[1]), `${log.batches.length}${log.more ? "+" : ""} of them since the last whole copy`)
            : value("The locked whole list", log.snapshot ? `stored under ${log.snapshot.object}` : "(the account is empty)"),
      P("And this is everything the server knows about your account. It was asked just now:"),
      h("pre", { class: "vf-pre" }, JSON.stringify(account, null, 1)),
      small("An id (made from your sign-in key, not from your words), when the account was made, and how many locked things there are and how big they are together. No name, no email, no password, no file names."));
  }

  function storedSection() {
    const v = E2E.vault;
    const files = [...v.nodes.values()].filter((n) => !n.dir && !n.trash && n.obj && n.key).sort((a, b) => (b.mtime || 0) - (a.mtime || 0)).slice(0, 60);
    const out = h("div", {}), listOut = h("div", {});
    const fail = (el) => (e) => el.replaceChildren(h("p", { class: "err" }, e.message || String(e)));
    const choose = h("select", { "aria-label": "A file in your account", onchange: () => { const n = v.nodes.get(choose.value); if (n) storedCheck(n, out).catch(fail(out)); } },
      h("option", { value: "" }, files.length ? "Choose one of your files…" : "There are no files in this account yet"),
      files.map((n) => h("option", { value: n.id }, `/${v.pathOf(n.id).join("/")} · ${size(n.size)}`)));
    return [
      ...section("Check a file that is really stored",
        P("The demonstration above used a key made for the occasion. This is the real thing: a file that is in your account now. The page asks the server for its copy and shows it to you as the server has it, then opens it here."),
        h("div", { class: "vf-actions" }, choose), out),
      ...section("Look at what the server knows",
        P("The names of your files are not on the server either. See for yourself:"),
        h("div", { class: "vf-actions" }, h("button", { class: "btn small", type: "button", onclick: () => listCheck(listOut).catch(fail(listOut)) }, "Ask the server")), listOut)];
  }

  // ------------------------------------------------------------ 3: from 24 words to keys
  async function wordsDemo(into) {
    const key = DDV.newKey(), phrase = await DDV.keyToPhrase(key), d = await DDV.derive(key);
    let locked = false;
    try { await crypto.subtle.exportKey("raw", d.meta); } catch (e) { locked = true; }
    into.replaceChildren(
      step(1, "24 words that are a key",
        P("These were made up just now, for this demonstration. ", h("b", {}, "They are not yours"), ", and they are forgotten when you leave. An account's words are made the same way, in the browser, when the account is made."),
        h("ol", { class: "phrase" }, phrase.split(" ").map((w) => h("li", {}, w))),
        bytes("The key the words stand for", key, "256 random bits, written as 24 words so that a person can copy them down", 32)),
      step(2, "Two keys are made from it, one way only",
        P("Think of a meat grinder: meat goes in, mince comes out, and nobody can turn mince back into the piece of meat. The key goes through one twice (HKDF-SHA256), which gives two other keys:"),
        value("The sign-in key", d.auth, "this is what the server gets. It proves you have the words; it can't be turned back into them"),
        value("The key of your list of files", locked ? "your browser has it, and refuses to hand it out, even to this page" : "(this browser handed it out: it should not)",
              "never sent anywhere; kept in this browser in a form that can be used but not read"),
        verdict(locked, locked ? "This page asked the browser to give it the key of the list in the clear. The browser said no." : "The browser gave the key out. It should keep it locked.")),
      step(3, "So the server never has what opens your files",
        P("It gets the sign-in key, and locked things. Your files are locked with keys that are in your list; your list is locked with a key made from your words; your words stay with you. "
          + "Somebody who takes everything the server has still holds nothing they can open.")));
  }

  function wordsSection() {
    const out = h("div", {});
    return section("From 24 words to keys",
      P("Your account is 24 words. This shows what becomes of them, with a set of words made for the purpose. Don't type your own words into anything on this page: nothing here needs them."),
      h("div", { class: "vf-actions" }, h("button", { class: "btn small", type: "button", onclick: () => wordsDemo(out).catch((e) => out.replaceChildren(h("p", { class: "err" }, e.message))) }, "Make demonstration words")), out);
  }

  // ------------------------------------------------------------ 4: checking the page itself
  async function pageCheck(into) {
    into.replaceChildren(P("Reading the page's own files…"));
    const head = await fetch("/", { credentials: "same-origin" });
    const csp = head.headers.get("Content-Security-Policy") || "";
    const rows = [];
    for (const name of SCRIPTS) {
      const r = await fetch("/" + name, { credentials: "same-origin", cache: "no-store" });
      rows.push([name, r.ok ? await sha256(new Uint8Array(await r.arrayBuffer())) : "(not there)"]);
    }
    const only = /default-src 'self'/.test(csp) && !/connect-src/.test(csp);
    into.replaceChildren(
      step(1, "This page may only talk to this server",
        P("A website can tell the browser where its pages may send things. This is what this one says. The browser enforces it, not us:"),
        h("pre", { class: "vf-pre" }, csp || "(none was sent)"),
        verdict(only, only ? "“default-src 'self'”: scripts, pictures and every request may only come from and go to this same address. Even a mistake in the page couldn't send your files or keys to another site."
                           : "This server did not send the rule that keeps the page to itself. Something between you and it may have removed it.")),
      step(2, "The code that runs here, and its fingerprints",
        P("These are the fingerprints (SHA-256) of the files your browser is running right now. The same files are public at ", h("a", { class: "link", href: REPO, target: "_blank", rel: "noopener" }, "github.com/xo907/ddrive"),
          `, in the folder ddrive/web. Version here: ${status && status.version ? status.version : "?"}.`),
        h("div", { class: "vf-prints" }, rows.map(([name, print]) => h("div", { class: "vf-print" },
          h("a", { class: "link", href: `${REPO}/blob/main/ddrive/web/${name}`, target: "_blank", rel: "noopener" }, name), h("code", {}, print)))),
        small("To check: download that version from GitHub and run “sha256sum ddrive/web/*.js” (Windows: “certutil -hashfile <file> SHA256”). If a fingerprint differs, the code you are being served is not the published code."),
        small("How the locking works is written out in ddrive/vault.py, and the browser's side of it is vaultcore.js: about 350 lines, meant to be read.")),
      step(3, "Watch it yourself",
        P("Press F12, open the Network tab, and upload a file. You will see requests to /api/vault/object carrying bytes that look like noise, under a random name, and a request to /api/vault/log that is noise too. "
          + "You will not find your file's name or its content in anything that is sent.")));
  }

  function pageSection() {
    const out = h("div", {});
    return section("Check the page itself",
      P("All of the above is shown to you by a page that came from the server. So the last question is the fair one: is the page honest? You can check that too."),
      h("div", { class: "vf-actions" }, h("button", { class: "btn small", type: "button", onclick: () => pageCheck(out).catch((e) => out.replaceChildren(h("p", { class: "err" }, e.message))) }, "Check this page")), out);
  }

  // ------------------------------------------------------------ the page
  const short = (...items) => h("ol", { class: "vf-short" }, items.map(([title, text]) => h("li", {}, h("b", {}, title), " ", text)));

  window.renderVerify = function () {
    const e2e = !!(status && status.e2e), account = isAccount();
    const can = !!(window.DDV && DDV.usable);
    const who = e2e ? [
      h("p", { class: "lede" }, "You shouldn't have to believe that your files are private. This page shows you, with your own files, in your own browser, and tells you how to check each thing yourself."),
      ...section("The short version",
        short(["Your 24 words are a key.", "They are made on your device and never leave it. Nobody else has them: not the server, not the people who run it, not us."],
              ["Every file is locked before it leaves.", "Your browser puts it in a locked box. Only your key opens the box."],
              ["The server only ever gets locked boxes.", "It doesn't know what is in them, and it doesn't know what they are called: the labels are locked too."],
              ["A box that was touched won't open.", "If anybody changes a single bit on the way, your browser notices and refuses it."],
              ["Lose the words and it is gone.", "Nobody can reset them or look them up. That is the proof that nobody else has them."])),
    ] : account ? [
      h("p", { class: "lede" }, "This account is of an earlier kind, and this page won't pretend otherwise: its files are encrypted, but the computer that runs this drive holds the key."),
      h("div", { class: "notice" }, "What that means: Discord can't read your files, and other accounts can't. Whoever runs this drive could. Accounts made now are end-to-end encrypted instead: their key never leaves the browser. ",
        "Yours can be moved into one (Your account → “Move it…”). Below you can see how those work."),
    ] : status && status.encrypted === false ? [
      h("p", { class: "lede" }, "This page shows how files are locked, with your own files, in your own browser. It starts with what is true of this drive in particular."),
      h("div", { class: "notice" }, "This drive was set up without encryption: its files go to Discord as they are, and Discord, and anybody who gets into the channel, can read them. ",
        "Accounts on this dashboard are different: they are end-to-end encrypted, locked in the browser. Below you can see how those work."),
    ] : [
      h("p", { class: "lede" }, "This page shows how files are locked, with your own files, in your own browser, and tells you how to check each thing yourself. It starts with what is true of this drive in particular."),
      ...section("The short version, for the owner's drive",
        short(["Your drive is locked before it goes to Discord.", "The computer that runs DDrive encrypts every file (AES-256-GCM) with the drive's key. Discord stores it and can't read a byte of it."],
              ["The key is your 24-word recovery phrase.", "It is on the computers you set the drive up on, and nowhere else."],
              ["So this computer can read the drive.", "It has to: it shows you the files as a drive letter. Whoever controls this computer can read them. That is the one difference from an account."],
              ["Accounts are locked one step earlier.", "An end-to-end encrypted account is locked in the browser, before the server gets anything. The server that runs them can't read them. “Move everything…” under Settings, Accounts moves your drive into one."])),
    ];
    const limits = section("What this can't promise",
      P("A page that only says good things isn't worth much. So:"),
      short(["The page comes from the server.", "If somebody took the server over, they could send you different code. That is true of every website that encrypts in the browser. The fingerprints above are how you catch it, and DDrive Lite (the program and the phone app) doesn't get its code from the server at all."],
            ["The server still sees some things.", "That locked things exist, about how big they are, when they come and go, and from which address. Not what they are."],
            ["Your device has to be yours.", "A device that is signed in holds the key. Somebody who has that device, or a program spying on it, can read what you can read."],
            ["Nobody can rescue a lost key.", "Without the 24 words the files stay locked for good. Keep them somewhere safe and offline."]));
    page(h("div", { class: "vf" },
      h("h1", {}, "Don't trust, verify"),
      ...who,
      ...(can ? lockSection() : [h("div", { class: "notice" }, "The demonstrations need an https address (or the computer itself): a browser only encrypts there. Open this dashboard by its https address to see them.")]),
      ...(can && e2e && window.E2E && E2E.vault ? storedSection() : []),
      ...(can ? wordsSection() : []),
      ...pageSection(),
      ...limits));
  };
})();
