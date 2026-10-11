"""Entry point of the lite-only Windows program (DiscordDriveLite.exe): a computer that uses a drive
running on another computer. Opened without arguments (e.g. double-clicked) it shows its menu."""

import sys

from discorddrive.lite import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["menu"]) or 0)
