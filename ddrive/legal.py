"""The terms and the privacy policy of a dashboard that other people have accounts on (/terms, /privacy).

They are written for whoever runs this copy of DDrive (the "operator"), whose name, contact and country
come from the settings, and they describe what the program really does with what people store. They
are a starting point written by the program's authors, not legal advice: an operator who lets the
public in should read them, change what doesn't fit and have them checked for where they are.
"""

import html

UPDATED = "11 October 2026"
QUOTA_NOTE = "The operator sets how much an account may store and may change that."


def _who(cfg):
    name = (getattr(cfg, "operator_name", "") or "").strip()
    return name or "the operator of this site (the person or organisation that runs it)"


def _contact(cfg):
    contact = (getattr(cfg, "operator_contact", "") or "").strip()
    return (f"You can reach the operator at {contact}." if contact
            else "You can reach the operator the way you were given this address.")


def _days(value):
    value = float(value or 0)
    return "without limit of time" if value <= 0 else f"for {value:g} day{'' if value == 1 else 's'}"


def terms(cfg):
    who = _who(cfg)
    region = (getattr(cfg, "legal_region", "") or "").strip()
    quota = int(getattr(cfg, "account_quota_bytes", 0) or 0)
    return "Terms and conditions", [
        ("", [f"These terms are an agreement between you and {who}, called “the operator” below. They cover this "
              "site and the accounts on it. By making an account, signing in, or using a link somebody shared with you "
              "from here, you agree to them. If you don't agree, don't use the site."]),
        ("1. What this is", [
            "This site runs DDrive, open-source software for storing files privately. An account gives you storage of "
            "your own for files, photos, notes, bookmarks, contacts and messages, and lets you share them with links.",
            "The operator runs this copy of the software and is the one providing the service to you. The authors of "
            "DDrive only publish the software: they don't run this site, can't see what is on it and have no part in "
            "this agreement.",
            "What you store is kept, encrypted, as attachments in a private channel on Discord, a service of Discord "
            "Inc. Neither this site nor DDrive is affiliated with, endorsed by or sponsored by Discord Inc.",
        ]),
        ("2. Your account is 24 words", [
            "An account has no name, email address or password. It is a phrase of 24 words that this site makes for "
            "you and shows you once. The words sign you in and are the key your data is encrypted with.",
            ["Keep the words secret. Anyone who has them has your account and everything in it.",
             "Keep the words safe. Nobody can look them up or reset them, the operator included. If you lose them, "
             "the account and its contents are lost for good.",
             "You are responsible for what is done with your account."],
            "You must be old enough to enter into an agreement like this where you live, and at least 16.",
        ]),
        ("3. What you may not do", [
            "You agree not to use the site to:",
            ["store, share or request anything illegal, including material that sexually exploits children, "
             "content that infringes somebody's copyright or other rights, or content you have no right to have;",
             "store or spread malware, or use share links for phishing, fraud or spam;",
             "harass, threaten or defame people, or publish private information about them without a right to;",
             "get at other accounts, the operator's own data or the server, or get around the limits of your account;",
             "overload the site, resell it, or use it as the storage of a service of your own without the operator's "
             "agreement;",
             "break the rules of a third party you bring into it. The Messages tool connects to Discord with your own "
             "Discord account: whether Discord allows that is between you and Discord, and using it is your decision "
             "and your risk."],
        ]),
        ("4. Your content", [
            "What you store stays yours. You give the operator only the permission needed to run the service for you: "
            "to store, copy, encrypt, transmit and show your content to you and to the people you share it with.",
            "You are responsible for your content and for having the right to store and share it. A share link gives "
            "what it points to, to anyone who has the link, until it expires or you turn it off.",
        ]),
        ("5. Limits", [
            (f"An account here may store up to {quota / 2 ** 30:g} GB, counting earlier versions and deleted files "
             "until they are removed. " if quota else "") + QUOTA_NOTE,
        ]),
        ("6. No promise that it stays, or that your data does", [
            "The service is offered as it is and as it is available. It can be slow, interrupted, changed or ended at "
            "any time, with or without notice.",
            "It depends on Discord, which the operator does not control. If Discord removes the data, limits access "
            "or changes its rules, what you stored can become unavailable or be lost, and nobody here can bring it back.",
            "So this is not a backup you can rely on alone: keep your own copy of anything you can't afford to lose.",
        ]),
        ("7. Switching off and deleting", [
            "You can delete your account at any time under “Your account”. Its contents are then removed from this "
            "server and from the storage; that can't be undone.",
            "The operator may switch off or delete an account, remove content or turn off a share link, without "
            "notice, when these terms are broken, when the law or a risk to the site or to others requires it, or "
            "when the service is ended.",
        ]),
        ("8. Reporting content", [
            f"If you believe something shared from this site is illegal or infringes your rights, tell the operator "
            f"which link it is and why. {_contact(cfg)}",
        ]),
        ("9. Liability", [
            "To the extent the law allows, the service comes without warranties of any kind, express or implied, "
            "including fitness for a particular purpose and that it is free of errors or secure against every attack.",
            "To the extent the law allows, the operator is not liable for lost data, lost profit, or indirect or "
            "consequential damage arising from your use of the service, and the operator's total liability is limited "
            "to what you paid for it in the twelve months before the claim (nothing, if it is free). Nothing here "
            "limits liability that the law does not allow to be limited, or the rights you have as a consumer.",
            "If your use of the service breaks these terms or the law and somebody makes a claim against the operator "
            "because of it, you agree to cover the resulting cost.",
        ]),
        ("10. Changes", [
            "The operator may change these terms. The date below says when they last changed. If you keep using the "
            "site after a change, you accept the new terms.",
        ]),
        ("11. Law and contact", [
            (f"These terms are governed by the law of {region}, and its courts decide disputes about them, unless the "
             "law where you live gives you a right to something else. " if region else "") + _contact(cfg),
            "If a part of these terms turns out not to be valid, the rest still applies.",
        ]),
    ]


def privacy(cfg):
    who = _who(cfg)
    versions = _days(getattr(cfg, "version_retention_days", 30))
    snapshots = _days(getattr(cfg, "snapshot_keep_days", 14))
    return "Privacy policy", [
        ("", [f"This explains what happens to your data on this site. The site is run by {who}, called “the "
              "operator” below, who is responsible for it (the “controller”, where data protection law uses that "
              f"word). {_contact(cfg)}"]),
        ("In short", [
            ["You are not asked for a name, an email address or a password. An account is 24 words.",
             "What you store is encrypted with a key made from your 24 words before it leaves this server for storage.",
             "There are no ads, no trackers, no analytics, and nothing is sold or used for anything but running the "
             "service.",
             "The server that runs the site handles your data to do its work, so you are trusting the operator, as "
             "with any hosted storage."],
        ]),
        ("1. What is kept about your account", [
            ["an account id, which is worked out from your words and does not reveal them;",
             "when the account was made and when it last signed in;",
             "the invite code it was made with, if there was one;",
             "how many files it holds and how much room they take;",
             "its encryption key, itself encrypted on the server. The server needs it to finish uploads, serve your "
             "share links and do background work while you are not signed in. The 24 words themselves are not stored."],
        ]),
        ("2. What you store", [
            "Files, photos, notes, bookmarks, contacts, saved messages, share links and the settings of your account.",
            ["Their contents are encrypted with your account's key and stored as attachments in a private channel on "
             "Discord (Discord Inc., United States). Discord receives encrypted data it cannot read, and can see how "
             "much there is and when it was stored. It does not learn who you are from this site.",
             "The list of your files (names, sizes, dates, folders) is kept on the operator's server, and an encrypted "
             "copy of it in the same channel.",
             "While a file is being uploaded or opened, the server holds it for a moment: an upload stays on its disk "
             "until it has been encrypted and stored, and recently opened parts are kept in a small cache.",
             f"Earlier versions of changed files and deleted files are kept {versions}, and snapshots {snapshots}, so "
             "that you can bring things back. After that they are removed."],
            "Your data is not readable by other accounts or by Discord. The operator does not look at it, but has the "
            "technical means to, since the server handles it. If that is not acceptable for something, don't store it here.",
        ]),
        ("3. Technical data", [
            ["Your IP address is used, in memory, to slow down repeated wrong sign-ins and to limit how many accounts "
             "one address can make. A failed sign-in is written to the server's log with the address it came from.",
             "What your account does is left out of the server's log, except error messages, which can name a file.",
             "If the site is reached through a proxy or hosting provider, that provider handles your requests too and "
             "may keep its own logs."],
        ]),
        ("4. Cookies and what your browser keeps", [
            ["one cookie that keeps you signed in, for up to 30 days or until you sign out. It is needed for the site "
             "to work;",
             "one cookie per password-protected share link you unlock, for 12 hours;",
             "a few display preferences (how the menu and file lists look), kept in your browser and never sent anywhere."],
            "There are no third-party cookies, and the pages load nothing from other sites.",
        ]),
        ("5. When the server contacts other sites for you", [
            ["When you add a bookmark or use “Save from a link”, the server fetches that address. The site at that "
             "address sees the server's request, not your browser.",
             "If you use Messages and log in with your own Discord account, the server keeps that login (encrypted) "
             "and connects to Discord as you, to record and archive the conversations you choose. Those contain other "
             "people's messages: you decide to keep them and are responsible for doing so. Logging out removes the login.",
             "A share link you make gives what it points to, to anyone who opens it, and counts how often it was opened."],
        ]),
        ("6. Who gets your data", [
            "Nobody, apart from: Discord Inc. as the storage described above; the hosting or network providers the "
            "operator uses to run the server; the people you give a share link to; and authorities, where the law "
            "obliges the operator. Data is not sold, rented or used for advertising.",
        ]),
        ("7. Why it is used", [
            "To provide the service you asked for by making an account (performing that agreement), and to keep the "
            "site secure and working (a legitimate interest of the operator and its users).",
        ]),
        ("8. How long", [
            "Until you delete things, or the account. Deleting the account under “Your account” removes its record "
            "and its files from the server right away, and what it stored on Discord shortly after, in the "
            "background. An account that was switched off keeps its data until it is deleted. The server's log is "
            "kept on the server until the operator clears it.",
        ]),
        ("9. Your choices and rights", [
            ["See and download everything you stored, at any time (folders download as a ZIP).",
             "Correct or delete any of it, or the whole account.",
             "Depending on where you live, you may also have the right to ask the operator what is held about you, to "
             "object to or restrict its use, and to complain to your data protection authority."],
            "Because an account has no name or address, the operator can only tell an account is yours if you are "
            "signed in to it, and cannot recover an account whose words are lost.",
        ]),
        ("10. Children", ["The site is not meant for anyone under 16."]),
        ("11. Changes", ["This policy may change; the date below says when it last did."]),
    ]


def page(kind, cfg):
    """One of the two documents as a web page."""
    title, sections = (terms if kind == "terms" else privacy)(cfg)
    esc = html.escape
    out = [f"<h1>{esc(title)}</h1>"]
    for heading, parts in sections:
        if heading:
            out.append(f"<h2>{esc(heading)}</h2>")
        for part in parts:
            if isinstance(part, list):
                out.append("<ul>" + "".join(f"<li>{esc(item)}</li>" for item in part) + "</ul>")
            elif part:
                out.append(f"<p>{esc(part)}</p>")
    other = ("privacy", "Privacy policy") if kind == "terms" else ("terms", "Terms and conditions")
    out.append(f'<p class="updated">Last updated {esc(UPDATED)}.</p>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{esc(title)}</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/app.css"></head>
<body class="legal"><main>
  <nav><a href="/"><img src="/logo.svg" alt="" width="22" height="22">DDrive</a>
  <span><a href="/{other[0]}">{other[1]}</a><a href="/">Sign in</a></span></nav>
  {''.join(out)}
</main></body></html>"""

