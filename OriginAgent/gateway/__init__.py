"""Gateway package — extracted HTTP/WS/REST/auth infrastructure.

Originally part of WebSocketChannel (3415-line God Class). The gateway
package splits it into focused, independently testable components.

Current components:
- auth.py: Token generation, validation, HMAC media URL signing
"""

from __future__ import annotations
