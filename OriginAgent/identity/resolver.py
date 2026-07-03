"""IdentityResolver — maps raw channel+sender signals to tenant_id.

Resolution chain (first match wins):
1. TenantRegistry lookup (channel + sender_id)
2. Pairing store lookup (approved pairings)
3. SpeakerRecognitionPlugin (if enabled and sample provided)
4. Guest / default fallback
"""

from __future__ import annotations

import importlib
from typing import Any

from loguru import logger

from OriginAgent.identity.speaker_plugin import SpeakerRecognitionPlugin
from OriginAgent.identity.tenant import Tenant, TenantRegistry
from OriginAgent.pairing.store import is_approved as pairing_is_approved


class IdentityResolver:
    """Resolve who is communicating through which channel."""

    def __init__(
        self,
        registry: TenantRegistry,
        *,
        speaker_plugin: SpeakerRecognitionPlugin | None = None,
        speaker_threshold: float = 0.7,
    ):
        self._registry = registry
        self._speaker_plugin = speaker_plugin
        self._speaker_threshold = speaker_threshold

    def resolve(
        self,
        channel: str,
        sender_id: str,
        *,
        speaker_sample: Any = None,
    ) -> Tenant:
        """Resolve a channel+sender_id to a Tenant.  Never returns None.

        *speaker_sample* is reserved for voice/face recognition data.
        """
        # Step 1: Direct channel binding
        tenant = self._registry.lookup(channel, str(sender_id))
        if tenant is not None and tenant.tenant_id != "guest":
            return tenant

        # Step 2: Pairing store (paired sender — need to ask which tenant)
        if pairing_is_approved(channel, str(sender_id)):
            # A paired but unbound sender: the pairing system approved them,
            # but no tenant claimed them yet.  Return a marker that tells
            # the caller to ask "which family member are you?"
            return Tenant(
                tenant_id="__pairing_pending__",
                display_name="New Device",
                unified_session_key=f"tenant:pairing:{channel}:{sender_id}",
                workspace_dir=self._registry._workspace / "tenants" / "_pairing",
                bdi_enabled=False,
            )

        # Step 3: Speaker recognition (reserved)
        if self._speaker_plugin is not None and speaker_sample is not None:
            try:
                result = self._speaker_plugin.recognize(speaker_sample)
                if result and result.confidence >= self._speaker_threshold:
                    t = self._registry.get(result.tenant_id)
                    if t is not None:
                        logger.info(
                            "Speaker recognition: {} → {} (conf={:.2f})",
                            channel, result.tenant_id, result.confidence,
                        )
                        return t
            except Exception:
                logger.exception("Speaker recognition plugin error")

        # Step 4: Guest fallback
        guest = self._registry.lookup(channel, "__guest__")
        if guest is not None:
            return guest

        # If absolutely nothing matches, return a synthetic guest
        return self._registry._guest_tenant or Tenant(
            tenant_id="guest",
            display_name="Guest",
            unified_session_key="tenant:guest",
            workspace_dir=self._registry._workspace / "tenants" / "guest",
            bdi_enabled=False,
        )

    @classmethod
    def from_config(
        cls,
        registry: TenantRegistry,
        speaker_config: Any | None = None,
    ) -> IdentityResolver:
        """Create resolver with optional speaker plugin loaded from config."""
        plugin: SpeakerRecognitionPlugin | None = None
        threshold = 0.7

        if speaker_config is not None and getattr(speaker_config, "enabled", False):
            plugin_path = getattr(speaker_config, "plugin", "")
            if plugin_path:
                try:
                    mod_name, cls_name = plugin_path.rsplit(".", 1)
                    mod = importlib.import_module(mod_name)
                    plugin_cls = getattr(mod, cls_name)
                    plugin = plugin_cls(**getattr(speaker_config, "config", {}))
                except Exception:
                    logger.exception("Failed to load speaker recognition plugin")
            threshold = getattr(speaker_config, "confidence_threshold", 0.7)

        return cls(registry, speaker_plugin=plugin, speaker_threshold=threshold)
