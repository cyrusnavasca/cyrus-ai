"""Test suite.

`src/` is put on the path here so the tests run against the working tree with no
install step - `python -m unittest discover -s tests` works on a clean checkout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
