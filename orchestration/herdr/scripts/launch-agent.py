#!/usr/bin/env python3
"""Start one fresh Herdr agent and optionally submit one prompt.

Thin entry point; the implementation lives in the herdr_launch package beside
this script. Python standard library only.
"""

import sys

from herdr_launch.cli import main


if __name__ == "__main__":
    sys.exit(main())
