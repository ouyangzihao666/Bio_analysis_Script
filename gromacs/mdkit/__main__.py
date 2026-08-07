"""Allow ``python -m mdkit``."""

import sys

from mdkit.cli import main


if __name__ == "__main__":
    sys.exit(main())
