"""Pytest bootstrap: make the project root importable as the ``app`` package.

This allows tests to run with ``python -m pytest`` from the repository root
without installing the package.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
