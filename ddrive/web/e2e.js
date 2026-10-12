"use strict";
/* Signing in to, and making, an end-to-end encrypted account. The 24 words are used here, in this
 * page, and never put into anything that is sent: the server gets a sign-in key made from them. */
(function () {
  const $ = (id) => document.getElementById(id);
  const say = (el, text) => { el.textContent = text; el.hidden = !text; };

  // ------------------------------------------------------------ signing in
  const form = document.querySelector('form[action="/login"]');
  if (form) {
    const words = form.querySelector("textarea"), go = form.querySelector("button[type=submit]");
    const note = document.createElement("p");
    note.className = "err"; note.setAttribute("role", "alert"); note.hidden = true;
    form.before(note);
    const old = document.createElement("div");
    old.hidden = true;
    old.innerHTML = '<p class="muted small">No end-to-end encrypted account here has these words. If they are the <b>drive owner\'s</b> ' +
      "recovery phrase, an <b>admin account's</b> words, or an account from before accounts were end-to-end encrypted, you can sign in the earlier way. " +
      "<b>That sends the words to this server.</b></p>" +
      '<button class="btn ghost" type="button">Send the words and sign in</button>';
    form.after(old);
    old.querySelector("button").addEventListener("click", () => { form.dataset.plain = "1"; form.submit(); });
    if (!DDV.usable) {
      const p = document.createElement("p");
      p.className = "muted small";
      p.textContent = "This address is not https, so the browser can't encrypt here: end-to-end encrypted accounts can't be opened " +
        "from it, and signing in sends the words to this server.";
      form.after(p);
      return;
    }
    form.addEventListener("submit", async (e) => {
      if (form.dataset.plain) return;
      e.preventDefault();
      say(note, ""); old.hidden = true; go.disabled = true;
      try {
        const key = await DDV.phraseToKey(words.value);
        const d = await DDV.derive(key);
        const r = await fetch("/login", { method: "POST", credentials: "same-origin", body: new URLSearchParams({ auth: d.auth }) });
        if (r.ok && r.redirected) {
          await DDV.keys.put(d.meta);
          words.value = "";
          location.replace("/");
          return;
        }
        if (r.status === 429) say(note, "Too many failed tries from here. Wait a few minutes and try again.");
        else old.hidden = false;
      } catch (err) {
        say(note, `That phrase isn't right: ${err.message}.`);
      }
      go.disabled = false;
    });
  }

  // ------------------------------------------------------------ making an account
  const make = $("e2e-make");
  if (make) {
    if (!DDV.usable) {
      $("e2e-start").hidden = true;
      $("e2e-no").hidden = false;
      return;
    }
    const list = $("words"), done = $("e2e-form"), copy = $("copy");
    make.addEventListener("click", async () => {
      const invite = $("e2e-invite");
      if (invite && !invite.value.trim()) { invite.focus(); return; }
      make.disabled = true;
      try {
        const key = DDV.newKey();
        const phrase = await DDV.keyToPhrase(key);
        if (DDV.phraseToKey && String(await DDV.phraseToKey(phrase)) !== String(key)) throw new Error("the words could not be checked");
        const d = await DDV.derive(key);
        list.replaceChildren(...phrase.split(" ").map((w) => { const li = document.createElement("li"); li.textContent = w; return li; }));
        done.elements.auth.value = d.auth;
        done.elements.invite.value = invite ? invite.value.trim() : done.elements.invite.value;
        done.addEventListener("submit", (e) => {
          // the key that opens the list of files is kept in this browser before the account is made
          if (done.dataset.kept) return;
          e.preventDefault();
          DDV.keys.put(d.meta).then(() => { done.dataset.kept = "1"; done.submit(); }, () => { done.dataset.kept = "1"; done.submit(); });
        });
        $("e2e-start").hidden = true;
        $("e2e-words").hidden = false;
        if (navigator.clipboard) {
          copy.hidden = false;
          copy.addEventListener("click", () => navigator.clipboard.writeText(phrase).then(() => { copy.textContent = "Copied. Paste them into a password manager."; }, () => {}));
        }
      } catch (err) {
        make.disabled = false;
        say($("e2e-err"), `The words could not be made: ${err.message}. Reload the page and try again.`);
      }
    });
  }
})();
