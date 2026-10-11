"""Entry point of the single-file Windows program (DDrive.exe).

Started without arguments (e.g. double-clicked) it opens the menu, like run.bat does.
"""

import sys

from ddrive.cli import main
from ddrive.menu import RESTART

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("menu")
    code = main() or 0
    sys.exit(0 if code == RESTART else code)
