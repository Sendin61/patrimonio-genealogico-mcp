"""Universal, source-agnostic genealogy investigation coordination."""

from .engine import UniversalInvestigationEngine
from .exa import ExaSourceAdapter
from .exa_store import ExaInvestigationStore
from .models import InvestigationTarget, SourceCapabilities
from .sources import GalicianaSourceAdapter, GenealogicalSourceAdapter
from .store import UniversalInvestigationStore

__all__ = [
    "GalicianaSourceAdapter",
    "ExaInvestigationStore",
    "ExaSourceAdapter",
    "GenealogicalSourceAdapter",
    "InvestigationTarget",
    "SourceCapabilities",
    "UniversalInvestigationEngine",
    "UniversalInvestigationStore",
]
