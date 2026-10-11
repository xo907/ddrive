"""Say which of the Android tests failed as error annotations, which can be read from the run's
summary page (and through the API) without signing in. The job's own log can't."""
import glob
import re

found = False
for path in glob.glob("android/*/build/test-results/**/*.xml", recursive=True):
    text = open(path, encoding="utf-8").read()
    for name, body in re.findall(r'<testcase name="([^"]+)"[^>]*>(.*?)</testcase>', text, re.S):
        m = re.search(r'<(?:failure|error) message="([^"]*)"', body)
        if m:
            found = True
            print(f"::error title={name}::" + m.group(1)[:900].replace("\n", " "))
if not found:
    print("::error title=Android build::The build failed before or outside the tests (it did not compile, or lint found an error).")
