#!/usr/bin/env python3
"""Start iMessage Explorer. See README.md, or run with --help."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 9):
    sys.exit("iMessage Explorer needs Python 3.9 or newer. You have %s." % sys.version.split()[0])

from imx.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
