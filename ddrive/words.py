"""The recovery phrase: the drive's 256-bit encryption key written as 24 ordinary words.

It is the BIP39 way of writing 256 bits (the standard English list of 2048 words, 11 bits a word,
the last word carrying an 8-bit checksum that catches a mistyped or misplaced word), so password
managers and people who know seed phrases recognise it. The phrase *is* the key: whoever has it can
read the drive, and nothing else is needed to set up a new device or to sign in to the dashboard.
"""

import hashlib
import os
import re

_WORDS = None


def wordlist():
    global _WORDS
    if _WORDS is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordlist.txt"), encoding="utf-8") as f:
            words = f.read().split()
        if len(words) != 2048:
            raise RuntimeError("the word list is damaged (reinstall DDrive)")
        _WORDS = words
    return _WORDS


def key_to_phrase(key: bytes) -> str:
    """24 words for a 32-byte key."""
    if len(key) != 32:
        raise ValueError("the key must be 32 bytes")
    words = wordlist()
    bits = (int.from_bytes(key, "big") << 8) | hashlib.sha256(key).digest()[0]       # 256 bits + 8 of checksum
    return " ".join(words[(bits >> (11 * i)) & 0x7FF] for i in range(23, -1, -1))


def looks_like_phrase(text) -> bool:
    return len(re.findall(r"[A-Za-z]+", str(text or ""))) >= 12 and not re.fullmatch(r"\s*[0-9a-fA-F]{64}\s*", str(text or ""))


def phrase_to_key(phrase: str) -> bytes:
    """The key a phrase stands for. Raises ValueError that says what is wrong (for the person typing)."""
    given = re.findall(r"[A-Za-z]+", str(phrase or "").lower())
    if len(given) != 24:
        raise ValueError(f"a recovery phrase has 24 words; this has {len(given)}")
    words = wordlist()
    index = {w: i for i, w in enumerate(words)}
    bits = 0
    for n, w in enumerate(given, 1):
        if w not in index:
            # the first four letters of every word are unique: say which word was probably meant
            close = [x for x in words if x[:4] == w[:4]]
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            raise ValueError(f"word {n}, '{w}', is not one of the words used{hint}")
        bits = (bits << 11) | index[w]
    key = (bits >> 8).to_bytes(32, "big")
    if hashlib.sha256(key).digest()[0] != bits & 0xFF:
        raise ValueError("the words don't fit together: one is wrong or out of order")
    return key


def grid(phrase: str, columns=4) -> str:
    """The phrase numbered in columns, for showing in a terminal."""
    words = phrase.split()
    rows = (len(words) + columns - 1) // columns
    lines = []
    for r in range(rows):
        cells = [f"{r + c * rows + 1:2}. {words[r + c * rows]:<10}" for c in range(columns) if r + c * rows < len(words)]
        lines.append("   " + "  ".join(cells).rstrip())
    return "\n".join(lines)
