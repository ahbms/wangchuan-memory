"""WangChuan legacy/compat namespace.

Use this namespace when you explicitly need the legacy conversation-history fallback.
Do not import legacy fallback factories from the main recall_service surface.

Boundary:
- this namespace is for explicit compatibility usage only
- it is not part of the default public facade exposed by `wangchuan`
- new consumers should not treat this namespace as a first-cut stable entry point
"""

from .legacy_fallback import create_legacy_recall_fallback

__all__ = ["create_legacy_recall_fallback"]
