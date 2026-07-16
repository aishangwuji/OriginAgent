"""Project-wide constants for OriginAgent."""


class RoleConstants:
    """Standard role name constants for LLM message API.

    Single source of truth for message role strings (rule 6).
    Use these instead of hardcoding ``"user"`` / ``"system"`` / ``"assistant"``
    in ``messages`` arrays exchanged with LLM providers.
    """

    USER = "user"
    SYSTEM = "system"
    ASSISTANT = "assistant"
