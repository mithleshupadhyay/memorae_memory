from __future__ import annotations

from dataclasses import dataclass, field

from memorae_memory.retrieval.context_policy import ContextPolicyConfig
from memorae_memory.retrieval.ranker import RankingConfig


@dataclass(frozen=True)
class MemoryEngineConfig:
    max_candidates: int = 80
    ranking: RankingConfig = field(default_factory=RankingConfig)
    context: ContextPolicyConfig = field(default_factory=ContextPolicyConfig)

    def __post_init__(self) -> None:
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1.")
