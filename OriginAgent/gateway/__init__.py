"""Gateway package — extracted HTTP/WS/REST/auth infrastructure.

Originally part of WebSocketChannel (3415-line God Class). The gateway
package splits it into focused, independently testable components.

Components:
- auth.py: Token generation, validation, HMAC media URL signing
- media_server.py: Signed media URL generation and static SPA serving
- rest_api.py: REST API handlers for /api/* and /webui/bootstrap routes
- _helpers.py: Shared helpers (base64, HTTP response constructors)
"""

from __future__ import annotations

from OriginAgent.gateway.auth import GatewayAuth
from OriginAgent.gateway.media_server import MediaServer
from OriginAgent.gateway.rest_api import RestApi

__all__ = [
    "GatewayAuth",
    "MediaServer",
    "RestApi",
]
