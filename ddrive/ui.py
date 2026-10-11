"""Shared terminal look (Made by XO.ST): the XO banner, colours, and a filter that gives every
command's output the same style, whether it runs inside the menu or on its own."""

import os
import re
import sys

from . import __version__

WINDOWS = sys.platform == "win32"


def _enable_ansi(stream):
    if os.environ.get("FORCE_COLOR") and not os.environ.get("NO_COLOR"):
        return True
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if WINDOWS:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            k32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            return False
    return True


ANSI = _enable_ansi(sys.stdout)
RED, GREEN, YELLOW, GRAY, BOLD, RESET = (
    ("\033[31m", "\033[32m", "\033[33m", "\033[90m", "\033[1m", "\033[0m") if ANSI else ("",) * 6)


def banner():
    print()
    print(f"  {BOLD}X {RED}O{RESET}   {BOLD}DDrive{RESET}  {GRAY}encrypted drive in your Discord channel{RESET}")
    print(f"       {GRAY}Made by XO.ST  |  v{__version__}{RESET}")
    print()


def embedded():
    """True when running as a page of the menu (the menu already shows the banner and title)."""
    return os.environ.get("DDRIVE_EMBEDDED") == "1"


# ------------------------------------------------------------- themed output
_LOG = re.compile(r"^\d\d:\d\d:\d\d \[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\] [\w.]+: (.*)$", re.S)
_ROW = re.compile(r"^(\s*)([A-Z][A-Za-z0-9 /()'.-]*:)(\s+)(.*)$", re.S)
_WORDS = [
    (re.compile(r"\b(RUNNING|MOUNTED \(Active\)|ENABLED|ON(?= \(AES))\b"), GREEN),
    (re.compile(r"\b(NOT (?:REACHABLE|FOUND|CONFIGURED|MOUNTED)|INVALID|OFF(?= -))"), RED),
]


def style_line(line):
    """Theme one line of output (without its newline)."""
    if not ANSI or not line.strip() or "\033[" in line:
        return line
    m = _LOG.match(line)
    if m:
        level, msg = m.groups()
        color = {"WARNING": YELLOW, "ERROR": RED, "CRITICAL": RED}.get(level, GRAY)
        return f"    {color}{msg}{RESET}"
    s = line.strip()
    lead = line[:len(line) - len(line.lstrip())]
    for tag, color in (("[SUCCESS]", GREEN), ("[OK]", GREEN), ("[ERROR]", RED), ("[FAIL]", RED),
                       ("[WARNING]", YELLOW), ("[!]", YELLOW), ("[INFO]", GRAY)):
        if s.startswith(tag):
            return f"{lead}{color}{tag}{RESET}{s[len(tag):]}"
    if set(s) <= {"="}:
        return f"{lead}{GRAY}{s}{RESET}"
    if s.startswith("---") and s.endswith("---"):
        return f"{lead}{RED}==>{RESET} {BOLD}{s.strip('- ').strip()}{RESET}"
    m = _ROW.match(line)
    if m:
        indent, label, gap, value = m.groups()
        for rx, color in _WORDS:
            value = rx.sub(lambda mm, c=color: f"{c}{mm.group(0)}{RESET}", value)
        return f"{indent}{GRAY}{label}{RESET}{gap}{value}"
    return line


class _Themed:
    """Wraps stdout/stderr. Text is collected until a line is complete, then themed and written;
    an unfinished line (a question prompt) is written as-is on flush(), which input() calls."""

    def __init__(self, raw):
        self._raw = raw
        self._buf = ""
        # inside a menu page, line output up with the page's left margin
        self._indent = "  " if embedded() else ""

    def _margin(self, line):
        return self._indent + line if self._indent and line.strip() and not line.startswith("  ") else line

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._raw.write(self._margin(style_line(line.rstrip("\r"))) + "\n")
        return len(text)

    def flush(self):
        if self._buf:
            self._raw.write(self._margin(self._buf))
            self._buf = ""
        self._raw.flush()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def theme_output():
    """Theme this process's stdout and stderr (only in a real terminal)."""
    if ANSI and not isinstance(sys.stdout, _Themed):
        import atexit
        sys.stdout = _Themed(sys.stdout)
        atexit.register(sys.stdout.flush)
        if _enable_ansi(sys.stderr):
            sys.stderr = _Themed(sys.stderr)
            atexit.register(sys.stderr.flush)
