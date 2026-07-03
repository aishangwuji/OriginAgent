"""Reserved interface for speaker/face recognition plugins.

This is a PROTOCOL — no implementation is shipped.  Third-party plugins
(vocalprint, face recognition, device proximity) implement this interface
and are loaded by the IdentityResolver at runtime.

Example future implementations:
- Vocalprint matching against enrolled voice samples per tenant
- Face recognition from camera feed on a home robot
- BLE device proximity (phone/watch MAC address → tenant)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RecognitionResult:
    tenant_id: str
    confidence: float          # 0.0–1.0
    method: str                # "vocalprint", "face", "ble_proximity"
    metadata: dict[str, Any]


@runtime_checkable
class SpeakerRecognitionPlugin(Protocol):
    """Protocol for identity recognition plugins.

    Plugins receive raw audio/video/sensor data and return a
    RecognitionResult if they can identify the speaker, or None.
    """

    @property
    def plugin_name(self) -> str: ...

    def enroll(self, tenant_id: str, sample: Any) -> bool:
        """Enroll a new identity sample for a tenant.  Returns success."""
        ...

    async def recognize(self, sample: Any) -> RecognitionResult | None:
        """Try to recognize who is speaking.  Returns None if uncertain."""
        ...

    def unenroll(self, tenant_id: str) -> bool:
        """Remove a tenant's enrollment data."""
        ...
