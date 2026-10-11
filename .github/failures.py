"""After a failed test run on GitHub: show which tests failed, and why, on the run's summary page
(as error annotations), so nobody has to dig through the log. Reads test-output.txt."""

import re


def esc(text):
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


text = open("test-output.txt", encoding="utf-8", errors="replace").read()
blocks = re.split(r"\n={70}\n", text)[1:]
for block in blocks[:9]:
    print("::error title=" + block.split("\n", 1)[0][:120] + "::" + esc(block[-1800:]))
if not blocks:
    print("::error title=The tests stopped::" + esc(text[-1800:]))
