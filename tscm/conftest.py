"""Make `sweep` importable without installing it.

pytest prepends a conftest's directory to `sys.path`, so placing this file at
the project root means `python -m pytest tscm/tests` works from the repository
root as well as from inside `tscm/`. Without it the tests only pass when run
from this directory, which is a trap for anyone running the whole repo's suite.
"""

import sys
from pathlib import Path

ROOT = str(Path(__file__).parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
