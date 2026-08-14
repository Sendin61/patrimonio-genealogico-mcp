"""Local-first AI providers for ROB Genealogy Lab."""

from .base import AIProvider, AIProviderStatus
from .interpreter import fallback_interpretation, interpret_research_request
from .llamacpp import LlamaCppProvider, LlamaCppRuntime

__all__ = [
    "AIProvider",
    "AIProviderStatus",
    "LlamaCppProvider",
    "LlamaCppRuntime",
    "fallback_interpretation",
    "interpret_research_request",
]
