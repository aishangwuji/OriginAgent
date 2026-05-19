"""Platform-aware content reading providers.

Selected provider details were adapted from feedgrab 0.22.0 (MIT).
"""

from OpenHome.integrations.content_read.reader import ContentReadError, ContentReader
from OpenHome.integrations.content_read.types import ContentReadResult

__all__ = ["ContentReadError", "ContentReadResult", "ContentReader"]
