from typing import Protocol

from app.models import Claim, Evidence


class EvidenceConnector(Protocol):
    async def fetch(self, claim: Claim, config: dict) -> list[Evidence]: ...
