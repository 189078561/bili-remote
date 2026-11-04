"""PT subtitle rendering for Bili Remote V2.

This package consumes the shared ResourceContext mapping. It has no metadata,
file, database, or publishing side effects.
"""

from .renderer import SubtitleService, render
from .router import create_router
from .memory import SubtitleRuleMemory, identity_key
from .rules import SubtitleRuleError, normalize_rule

__all__ = ["SubtitleRuleError", "SubtitleRuleMemory", "SubtitleService", "create_router",
           "identity_key", "normalize_rule", "render"]
