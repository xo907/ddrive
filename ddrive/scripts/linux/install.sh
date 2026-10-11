#!/usr/bin/env bash
# DDrive installer for Debian / Ubuntu / Raspberry Pi OS.
# Usage: ddrive/scripts/linux/install.sh [mount-dir]   (default: /mnt/ddrive)
# Normally started from ./run.sh -> Tools -> "Install / repair requirements".
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT="$(cd "$DIR/../../.." >/dev/null 2>&1 && pwd)"   # the DDrive folder
MOUNT_DIR="${1:-/mnt/ddrive}"

# Privileged steps run directly when already root (e.g. on a VPS), otherwise through sudo.
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    echo "[ERROR] This installer needs root rights. Run it as root, or install sudo first." >&2
    exit 1
fi
ME="$(id -un)"
MYGROUP="$(id -gn)"

echo "============================================================"
echo "          DDrive - Debian / Linux Installer"
echo "============================================================"

echo "[1/4] Installing dependencies (libfuse2, python3-cryptography, curl)..."
$SUDO apt-get update -qq || echo "  [WARNING] 'apt-get update' reported errors; continuing with the package lists we have."

# Install one package at a time so one broken package can't block the others.
# A security update whose file is missing from the mirror (seen on end-of-life
# releases) is retried with the release's own version of the package.
CODENAME="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-}")"
install_pkg() {
    $SUDO apt-get install -y "$1" && return 0
    if [ -n "$CODENAME" ]; then
        echo "  -> Retrying $1 with the version from '$CODENAME'..."
        $SUDO apt-get install -y -t "$CODENAME" "$1" && return 0
    fi
    return 1
}

install_pkg python3 || { echo "[ERROR] Could not install python3." >&2; exit 1; }
install_pkg curl || echo "  [WARNING] curl is not installed; DDrive will use Python's built-in HTTP client."

# The FUSE 2 library (renamed libfuse2t64 on Debian 13+ / Ubuntu 24.04+).
if apt-cache show libfuse2t64 >/dev/null 2>&1; then LIBFUSE=libfuse2t64; else LIBFUSE=libfuse2; fi
install_pkg "$LIBFUSE" || { echo "[ERROR] Could not install $LIBFUSE." >&2; exit 1; }

# The fusermount tool comes from 'fuse' (FUSE 2) or 'fuse3'; either works. Never swap
# one for the other: removing an installed fuse3 could break other software.
if command -v fusermount >/dev/null 2>&1 || command -v fusermount3 >/dev/null 2>&1; then
    echo "  -> fusermount already available."
elif apt-cache show fuse3 >/dev/null 2>&1; then
    install_pkg fuse3 || install_pkg fuse || echo "  [WARNING] Could not install fuse3/fuse (fusermount)."
else
    install_pkg fuse || echo "  [WARNING] Could not install fuse (fusermount)."
fi

if ! python3 -c "import cryptography" 2>/dev/null; then
    if ! install_pkg python3-cryptography; then
        echo "  -> Trying pip instead..."
        python3 -m pip install --user cryptography 2>/dev/null \
            || { install_pkg python3-pip && python3 -m pip install --user cryptography; } \
            || echo "  [WARNING] Could not install python3-cryptography; encryption will not work on this machine."
    fi
fi

echo "[2/4] Enabling user_allow_other in /etc/fuse.conf (lets Samba / other users read the mount)..."
if [ -f /etc/fuse.conf ]; then
    $SUDO sed -i 's/^#\s*user_allow_other/user_allow_other/' /etc/fuse.conf
fi
grep -q "^user_allow_other" /etc/fuse.conf 2>/dev/null || echo "user_allow_other" | $SUDO tee -a /etc/fuse.conf >/dev/null

if [ ! -e /dev/fuse ]; then
    echo "  [WARNING] /dev/fuse does not exist. This machine may not support FUSE (common on"
    echo "            OpenVZ/LXC container VPSes); ask your provider to enable FUSE, or use a KVM VPS."
fi

echo "[3/4] Creating mount point $MOUNT_DIR..."
$SUDO mkdir -p "$MOUNT_DIR"
$SUDO chown "$ME:$MYGROUP" "$MOUNT_DIR"

echo "[4/4] Making scripts executable..."
chmod +x "$ROOT/run.sh" "$DIR"/*.sh

echo ""
echo "============================================================"
echo "Installation complete. Next steps:"
echo ""
echo "  Open the menu and choose Setup, then Start:"
echo "       $ROOT/run.sh"
echo ""
echo "  (Using the drive on another computer too? Use the same bot token,"
echo "   channel and encryption password there.)"
echo "============================================================"
