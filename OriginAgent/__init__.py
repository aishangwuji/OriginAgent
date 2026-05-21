"""OriginAgent compatibility package.

The implementation still lives under ``OpenHome`` during the staged rename.
"""

from OpenHome import __logo__, __version__
from OpenHome.OpenHome import OpenHome, RunResult

OriginAgent = OpenHome

__all__ = ["OriginAgent", "OpenHome", "RunResult", "__logo__", "__version__"]
