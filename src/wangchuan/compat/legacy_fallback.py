from __future__ import annotations

"""Legacy fallback compat surface for WangChuan.

This module exists so callers that truly need the retired conversation-history
fallback can opt into it explicitly, instead of importing it from the main
recall_service surface.
"""

from ..chat_memory import create_chat_memory as _create_legacy_chat_memory


def create_legacy_recall_fallback(db_path: str | None = None):
    """Create the legacy recall fallback (compat sidecar).

    Notes:
    - explicit compat namespace: `wangchuan.compat`
    - avoids keeping legacy fallback factory on the main recall_service surface
    - supports scoped db injection for tests and migration scripts
    """
    return _create_legacy_chat_memory(db_path)


__all__ = ["create_legacy_recall_fallback"]
