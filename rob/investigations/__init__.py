"""Universal, source-agnostic genealogy investigation coordination."""

from .engine import UniversalInvestigationEngine
from .models import InvestigationTarget, SourceCapabilities
from .sources import GalicianaSourceAdapter, GenealogicalSourceAdapter
from .store import UniversalInvestigationStore

__all__ = [
    "GalicianaSourceAdapter",
    "GenealogicalSourceAdapter",
    "InvestigationTarget",
    "SourceCapabilities",
    "UniversalInvestigationEngine",
    "UniversalInvestigationStore",
]
