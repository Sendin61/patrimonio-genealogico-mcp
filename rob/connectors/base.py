from abc import ABC, abstractmethod
from ..models import GenealogyQuery, SearchResult


class BaseConnector(ABC):
    source_id: str

    @abstractmethod
    async def search(self, query: GenealogyQuery, limit: int = 20) -> list[SearchResult]:
        raise NotImplementedError

    async def healthcheck(self) -> dict[str, str | bool]:
        return {"source_id": self.source_id, "ok": True}
