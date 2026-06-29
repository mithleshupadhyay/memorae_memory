from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import sqrt

from memorae_memory.retrieval.bm25 import tokenize
from memorae_memory.schemas import EventRecord, EventSignal, QueryIntent

STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "at",
    "be",
    "been",
    "by",
    "can",
    "do",
    "everything",
    "for",
    "from",
    "have",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "related",
    "should",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "which",
    "with",
}
SUMMARY_TERMS = {"summarize", "summary", "recap", "status", "related", "everything"}
TODAY_TERMS = {"today", "focus", "priority", "prioritize", "now"}
RISK_TERMS = {"risk", "missing", "miss", "commitment", "commitments", "deadline", "due"}
PROCRASTINATION_TERMS = {
    "procrastinating",
    "procrastinate",
    "delayed",
    "stalling",
    "putting",
    "slipping",
}
TOPIC_QUERY_STOPWORDS = STOPWORDS | SUMMARY_TERMS | TODAY_TERMS | RISK_TERMS | PROCRASTINATION_TERMS


@dataclass(frozen=True)
class QueryProfile:
    query: str
    intent: QueryIntent
    query_terms: set[str]
    selected_topics: set[str] = field(default_factory=set)
    topic_scores: dict[str, float] = field(default_factory=dict)


class QueryProfiler:
    def __init__(
        self,
        events: list[EventRecord],
        signals: dict[int, EventSignal],
    ) -> None:
        self.events = events
        self.signals = signals
        self.topic_terms = _build_topic_terms(events=events, signals=signals)

    def analyze(self, query: str) -> QueryProfile:
        query_terms = {term for term in tokenize(query) if term not in STOPWORDS}
        topic_query_terms = {term for term in query_terms if term not in TOPIC_QUERY_STOPWORDS}
        topic_scores = _score_topics_for_query(
            topic_terms=self.topic_terms,
            query_terms=topic_query_terms,
        )

        return QueryProfile(
            query=query,
            intent=_classify_intent(query),
            query_terms=query_terms,
            selected_topics=_select_query_topics(topic_scores),
            topic_scores=topic_scores,
        )


def _build_topic_terms(
    events: list[EventRecord],
    signals: dict[int, EventSignal],
) -> dict[str, Counter[str]]:
    topic_terms: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        signal = signals[event.event_id]
        useful_terms = [
            term for term in tokenize(event.content) if term not in STOPWORDS and len(term) > 2
        ]
        for topic in signal.topics:
            topic_terms[topic].update(useful_terms)
            topic_terms[topic].update(tokenize(topic.replace("_", " ")))
    return dict(topic_terms)


def _score_topics_for_query(
    topic_terms: dict[str, Counter[str]],
    query_terms: set[str],
) -> dict[str, float]:
    if not query_terms:
        return {}

    topic_scores: dict[str, float] = {}
    normalizer = sqrt(len(query_terms))
    for topic, terms in topic_terms.items():
        overlap_score = 0.0
        for term in query_terms:
            if term not in terms:
                continue
            overlap_score += 1.0 + min(terms[term], 4) * 0.25

        label_terms = set(tokenize(topic.replace("_", " ")))
        label_overlap = query_terms & label_terms
        if label_overlap:
            overlap_score += 1.25 * len(label_overlap)

        if overlap_score > 0:
            topic_scores[topic] = round(overlap_score / normalizer, 3)

    return dict(sorted(topic_scores.items(), key=lambda item: item[1], reverse=True))


def _select_query_topics(topic_scores: dict[str, float]) -> set[str]:
    if not topic_scores:
        return set()

    best_score = max(topic_scores.values())
    threshold = max(1.2, best_score * 0.7)
    return {topic for topic, score in topic_scores.items() if score >= threshold}


def _classify_intent(query: str) -> QueryIntent:
    query_terms = set(tokenize(query))
    query_lower = query.lower()
    if query_terms & PROCRASTINATION_TERMS or "putting off" in query_lower:
        return QueryIntent.PROCRASTINATION
    if query_terms & RISK_TERMS:
        return QueryIntent.RISK_MISSING
    if query_terms & TODAY_TERMS:
        return QueryIntent.TODAY_FOCUS
    if query_terms & SUMMARY_TERMS:
        return QueryIntent.TOPIC_SUMMARY
    return QueryIntent.GENERIC
