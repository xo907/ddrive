#!/usr/bin/env bash
# Builds dist/ddrive_<version>_all.deb (Debian, Ubuntu, Raspberry Pi OS).
# Usage: packaging/build_deb.sh        (needs dpkg-deb; run on Linux or in CI)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' ddrive/__init__.py)"
PKG="build/deb/ddrive_${VERSION}_all"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/ddrive" "$PKG/usr/bin" "$PKG/usr/lib/systemd/user"

cp -r ddrive run.sh README.md CHANGELOG.md LICENSE "$PKG/opt/ddrive/"
find "$PKG/opt/ddrive" -name "__pycache__" -prune -exec rm -rf {} +
find "$PKG/opt/ddrive" -type d -exec chmod 755 {} +
find "$PKG/opt/ddrive" -type f -exec chmod 644 {} +
chmod 755 "$PKG/opt/ddrive/run.sh" "$PKG/opt/ddrive/ddrive/scripts/linux/install.sh"

cat > "$PKG/usr/bin/ddrive" <<'EOF'
#!/bin/sh
# DDrive - Made by XO.ST. With no arguments: the menu.
exec /opt/ddrive/run.sh "$@"
EOF
chmod 755 "$PKG/usr/bin/ddrive"

cat > "$PKG/usr/lib/systemd/user/ddrive.service" <<'EOF'
# Start the drive when you log in:  systemctl --user enable --now ddrive
# Keep it running without a login:  sudo loginctl enable-linger "$USER"
[Unit]
Description=DDrive encrypted Discord-backed drive
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/ddrive mount
ExecStop=/usr/bin/ddrive stop
KillSignal=SIGTERM
TimeoutStopSec=60
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

SIZE="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: ddrive
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-cryptography, libfuse2t64 | libfuse2, fuse3 | fuse, curl
Installed-Size: ${SIZE}
Maintainer: XO.ST <https://github.com/xo907/ddrive>
Homepage: https://github.com/xo907/ddrive
Description: Encrypted drive backed by a private Discord channel
 DDrive mounts a private Discord channel as an encrypted folder, keeps
 several devices in sync, rebuilds pieces Discord loses from spare pieces,
 and has a web dashboard. Run "ddrive" for the menu.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
# Let the drive be shared with other users / Samba when allow_other is set.
if [ -f /etc/fuse.conf ] && ! grep -q '^[[:space:]]*user_allow_other' /etc/fuse.conf; then
    echo "user_allow_other" >> /etc/fuse.conf
fi
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"

mkdir -p dist
dpkg-deb --build --root-owner-group "$PKG" "dist/ddrive_${VERSION}_all.deb"
echo "Built dist/ddrive_${VERSION}_all.deb"

# The lite-only package: a computer that uses a drive running on another computer.
LITE="build/deb/ddrive-lite_${VERSION}_all"
rm -rf "$LITE"
mkdir -p "$LITE/DEBIAN" "$LITE/opt/ddrive-lite/ddrive/_vendor" "$LITE/usr/bin"
for f in __init__.py lite.py litesync.py vault.py words.py wordlist.txt sync.py locks.py config.py legacy.py secretbox.py crypto.py fuse_loader.py runtime.py; do
    cp "ddrive/$f" "$LITE/opt/ddrive-lite/ddrive/"
done
cp ddrive/_vendor/*.py "$LITE/opt/ddrive-lite/ddrive/_vendor/"
cp LICENSE "$LITE/opt/ddrive-lite/"
find "$LITE/opt/ddrive-lite" -type d -exec chmod 755 {} +
find "$LITE/opt/ddrive-lite" -type f -exec chmod 644 {} +

cat > "$LITE/opt/ddrive-lite/run.sh" <<'EOF'
#!/bin/sh
# DDrive Lite - Made by XO.ST. "run.sh lite <command>", as the autostart entry calls it.
[ "$1" = "lite" ] && shift
exec /usr/bin/ddrive-lite "$@"
EOF
cat > "$LITE/usr/bin/ddrive-lite" <<'EOF'
#!/bin/sh
# DDrive Lite - Made by XO.ST. With no arguments: the menu.
export PYTHONPATH="/opt/ddrive-lite${PYTHONPATH:+:$PYTHONPATH}" DDRIVE_LITE_ONLY=1
[ $# -eq 0 ] && set -- menu
exec python3 -c 'import sys; from ddrive.lite import main; sys.exit(main(sys.argv[1:]) or 0)' "$@"
EOF
chmod 755 "$LITE/usr/bin/ddrive-lite" "$LITE/opt/ddrive-lite/run.sh"

SIZE="$(du -sk "$LITE" | cut -f1)"
cat > "$LITE/DEBIAN/control" <<EOF
Package: ddrive-lite
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-cryptography, libfuse2t64 | libfuse2, fuse3 | fuse
Installed-Size: ${SIZE}
Maintainer: XO.ST <https://github.com/xo907/ddrive>
Homepage: https://github.com/xo907/ddrive
Description: Use a DDrive that runs on another computer
 DDrive Lite shows the drive of a computer that runs DDrive (the
 host) as a folder here, and streams, downloads and uploads through it. It
 keeps no bot, key or copy of the drive, and can sync folders of this
 computer with the drive. Run "ddrive-lite" for the menu.
EOF
dpkg-deb --build --root-owner-group "$LITE" "dist/ddrive-lite_${VERSION}_all.deb"
echo "Built dist/ddrive-lite_${VERSION}_all.deb"
