"""HTTP route handlers extracted from the WebSocket channel (D2).

Each sub-module registers its routes against a shared dispatch table.
"""

from __future__ import annotations

from typing import Callable

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

RouteHandler = Callable[[WsRequest], Response]
RouteTable = list[tuple[str, RouteHandler]]  # (path_pattern, handler)
