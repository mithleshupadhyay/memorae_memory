from __future__ import annotations

import math
import re
from collections import Counter

from memorae_memory.shared.schemas import EventRecord


TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_'-]*", flags=re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


class BM25Index:
    def __init__(self, events: list[EventRecord], extra_terms: dict[int, list[str]] | None = None):
        self.events = events
        self._term_counts: list[Counter[str]] = []
        self._doc_frequency: Counter[str] = Counter()
        self._doc_lengths: list[int] = []

        for event in events:
            terms = tokenize(event.content)
            if extra_terms:
                terms.extend(extra_terms.get(event.event_id, []))
            term_counts = Counter(terms)
            self._term_counts.append(term_counts)
            self._doc_frequency.update(term_counts.keys())
            self._doc_lengths.append(sum(term_counts.values()))

        self._average_doc_length = (
            sum(self._doc_lengths) / len(self._doc_lengths) if self._doc_lengths else 0.0
        )

    def score(self, query: str, event_id: int) -> float:
        query_terms = tokenize(query)
        if not query_terms:
            return 0.0

        term_counts = self._term_counts[event_id]
        doc_length = self._doc_lengths[event_id]
        score = 0.0
        k1 = 1.5
        b = 0.75
        corpus_size = max(len(self.events), 1)

        for term in query_terms:
            frequency = term_counts.get(term, 0)
            if frequency <= 0:
                continue

            document_frequency = self._doc_frequency.get(term, 0)
            idf = math.log(1 + ((corpus_size - document_frequency + 0.5) / (document_frequency + 0.5)))
            normalization = frequency + k1 * (
                1 - b + b * (doc_length / max(self._average_doc_length, 1.0))
            )
            score += idf * ((frequency * (k1 + 1)) / normalization)

        return score
