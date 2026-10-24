"""Smallest check that a mistyped certificate fingerprint is not silently ignored.

Run from the repo root:  python -m tests.test_web_fingerprint

The rules live in web/remotephone.js. web/test/fingerprint.mjs reads them out of that file and
checks them, so this only needs Node, not a browser.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    node = shutil.which("node")
    if not node:
        print("skipped: node is not installed")
        return 0
    return subprocess.run([node, str(ROOT / "web" / "test" / "fingerprint.mjs")]).returncode


if __name__ == "__main__":
    sys.exit(main())
