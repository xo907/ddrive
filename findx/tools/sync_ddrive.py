"""Copies FindX's map into DDrive (ddrive/web/findx), where the dashboard's FindX page and shared links use it.

The app's copy (app/src/main/assets/map) is the one to change. DDrive gets the same scripts and styles,
and its two pages are made from the app's index.html: the dashboard's FindX page (end-to-end account
support and findx-dashboard.js as the source of data) and a shared link's page (findx-share.js). Its own
files (findx-dashboard.js, findx-panel.*, findx-join.*, findx-dash.css, join.html) are left alone.

    python tools/sync_ddrive.py [path to the DDrive checkout; by default the one FindX is in]
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(HERE, "app", "src", "main", "assets", "map")
SHARED = ["findx-core.js", "findx-style.js", "findx-map.js", "findx-share.js", "findx.css"]


def absolute(html):
    html = html.replace('href="vendor/', 'href="/findx/vendor/').replace('src="vendor/', 'src="/findx/vendor/')
    return re.sub(r'(href|src)="(findx[-.\w]*\.(?:js|css))"', r'\1="/findx/\2"', html)


def main(ddrive):
    out = os.path.join(ddrive, "ddrive", "web", "findx")
    os.makedirs(os.path.join(out, "vendor"), exist_ok=True)
    for f in SHARED:
        shutil.copyfile(os.path.join(MAP, f), os.path.join(out, f))
    for f in os.listdir(os.path.join(MAP, "vendor")):
        shutil.copyfile(os.path.join(MAP, "vendor", f), os.path.join(out, "vendor", f))
    with open(os.path.join(MAP, "index.html"), encoding="utf-8") as f:
        src = f.read()

    def swap(html, old, new):
        assert old in html, old
        return html.replace(old, new)

    dash = absolute(src)
    dash = swap(dash, "<title>FindX</title>", '<title>FindX · DDrive</title>\n<link rel="icon" href="/logo.svg" type="image/svg+xml">\n'
                                             '<link rel="stylesheet" href="/findx/findx-dash.css">')
    dash = swap(dash, '<script src="/findx/vendor/maplibre-gl.js"></script>',
                '<script src="/vaultcore.js"></script>\n<script src="/e2e-api.js"></script>\n<script src="/findx/vendor/maplibre-gl.js"></script>')
    dash = swap(dash, '<script src="/findx/findx-source.js"></script>', '<script src="/findx/findx-dashboard.js"></script>')
    # The Manage panel: everything the app does, for your phones and circles (DDrive's own files).
    dash = swap(dash, '<link rel="stylesheet" href="/findx/findx.css">',
                '<link rel="stylesheet" href="/findx/findx.css">\n<link rel="stylesheet" href="/findx/findx-panel.css">')
    dash = swap(dash, '<script src="/findx/findx-map.js"></script>',
                '<script src="/findx/findx-map.js"></script>\n<script src="/findx/findx-panel.js"></script>')
    dash = swap(dash, "FindX shows this phone here as soon as it knows where it is.",
                "Your devices and the people in your circles show here once FindX on a phone is linked to this DDrive.")
    share = absolute(src)
    share = swap(share, "<title>FindX</title>", '<title>Live location · FindX</title>\n<meta name="robots" content="noindex, nofollow">')
    share = swap(share, '<script src="/findx/findx-source.js"></script>', '<script src="/findx/findx-share.js"></script>')
    share = swap(share, '<h2 id="emptyTitle">No position yet</h2><p id="emptyText">FindX shows this phone here as soon as it knows where it is.</p>',
                 '<h2 id="emptyTitle">Waiting for a position</h2><p id="emptyText">The location shows here as soon as the phone sends one.</p>')
    for name, html in (("index.html", dash), ("share.html", share)):
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    print("FindX map copied to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(HERE))       # (FindX lives in DDrive's repository, in findx/)
