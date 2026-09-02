from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from typing import TYPE_CHECKING

from autocomplete.matcher import match_and_score as _match_and_score
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.ranking import TopKSelector
from autocomplete.scoring import exact_score as _exact_score

if TYPE_CHECKING:
    from autocomplete.index import SearchIndex


def _normalize(prefix: str) -> str:
    from autocomplete.normalization import normalize

    return normalize(prefix)


class SearchEngine:
    def __init__(
        self,
        records_by_id: dict[int, SentenceRecord],
        index: SearchIndex,
        *,
        measure_exact_path: bool = False,
    ):
        self._records_by_id = records_by_id
        self._index = index
        self._measure_exact_path = measure_exact_path
        self._last_exact_metrics = ExactPathMetrics()

    @property
    def last_exact_metrics(self) -> ExactPathMetrics:
        return self._last_exact_metrics

    def search(
        self,
        prefix: str,
        k: int = 5,
    ) -> list[AutoCompleteData]:
        self._last_exact_metrics = ExactPathMetrics()
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("k must be an integer")
        if k < 0:
            raise ValueError("k must be non-negative")
        if k == 0:
            return []

        normalized_query = _normalize(prefix)
        if normalized_query == "":
            return []

        selector = TopKSelector(k)
        maximum_score = _exact_score(len(normalized_query))
        exact_sentence_ids: set[int] = set()
        add_exact = selector._add_equal_score_fields
        direct_posting = len(normalized_query) <= 3
        if direct_posting:
            get_precomputed = getattr(
                self._index,
                "get_precomputed_exact_top_k",
                None,
            )
            precomputed = (
                get_precomputed(normalized_query)
                if get_precomputed is not None
                else None
            )
            if (
                precomputed is not None
                and precomputed.exact_occurrence_count >= 5
                and k <= 5
            ):
                exact_started = perf_counter_ns() if self._measure_exact_path else 0
                for sentence_id in precomputed.sentence_ids[:k]:
                    record = self._records_by_id[sentence_id]
                    add_exact(
                        score=maximum_score,
                        completed_sentence=record.original,
                        source_text=record.source_path,
                        offset=record.line_number,
                    )
                if self._measure_exact_path:
                    self._last_exact_metrics = ExactPathMetrics(
                        candidates=precomputed.exact_occurrence_count,
                        records_examined=k,
                        confirmed=precomputed.exact_occurrence_count,
                        result_allocations=k,
                        elapsed_ns=perf_counter_ns() - exact_started,
                    )
                return selector.results()
            exact_candidates = self._index.get_exact_candidate_ids(normalized_query)
            if self._measure_exact_path:
                exact_started = perf_counter_ns()
                allocations = 0
                for sentence_id in exact_candidates:
                    record = self._records_by_id[sentence_id]
                    if selector.could_accept(
                        score=maximum_score,
                        completed_sentence=record.original,
                        source_text=record.source_path,
                        offset=record.line_number,
                    ):
                        allocations += 1
                    add_exact(
                        score=maximum_score,
                        completed_sentence=record.original,
                        source_text=record.source_path,
                        offset=record.line_number,
                    )
                candidate_count = len(exact_candidates)
                self._last_exact_metrics = ExactPathMetrics(
                    candidates=candidate_count,
                    records_examined=candidate_count,
                    confirmed=candidate_count,
                    alphabetical_rejections=candidate_count - allocations,
                    result_allocations=allocations,
                    elapsed_ns=perf_counter_ns() - exact_started,
                )
            else:
                for sentence_id in exact_candidates:
                    record = self._records_by_id[sentence_id]
                    add_exact(
                        score=maximum_score,
                        completed_sentence=record.original,
                        source_text=record.source_path,
                        offset=record.line_number,
                    )
            if len(exact_candidates) >= k:
                return selector.results()
            exact_sentence_ids.update(exact_candidates)
        else:
            exact_started = perf_counter_ns() if self._measure_exact_path else 0
            candidate_count = 0
            substring_checks = 0
            allocations = 0
            planned_exact = getattr(
                self._index,
                "iter_exact_candidate_ids_if_at_least",
                None,
            )
            exact_candidates = (
                planned_exact(normalized_query, k)
                if planned_exact is not None
                else self._index.iter_exact_candidate_ids(normalized_query)
            )
            for sentence_id in exact_candidates:
                if self._measure_exact_path:
                    candidate_count += 1
                    substring_checks += 1
                record = self._records_by_id[sentence_id]
                if normalized_query not in record.normalized:
                    continue
                exact_sentence_ids.add(sentence_id)
                if self._measure_exact_path and selector.could_accept(
                    score=maximum_score,
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                ):
                    allocations += 1
                add_exact(
                    score=maximum_score,
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                )
            confirmed_count = len(exact_sentence_ids)
            if self._measure_exact_path:
                self._last_exact_metrics = ExactPathMetrics(
                    candidates=candidate_count,
                    records_examined=candidate_count,
                    substring_checks=substring_checks,
                    confirmed=confirmed_count,
                    alphabetical_rejections=confirmed_count - allocations,
                    result_allocations=allocations,
                    elapsed_ns=perf_counter_ns() - exact_started,
                )
            if confirmed_count >= k:
                return selector.results()

        # Candidate IDs are sentence-ID ordered, not rank ordered. The complete
        # stream must be scanned, but this global score bound can safely avoid the
        # matcher when even a maximum-scoring candidate cannot enter TOP K.
        candidate_ids = self._index.iter_candidate_ids(normalized_query)
        if exact_sentence_ids:
            candidate_ids = (
                sentence_id
                for sentence_id in candidate_ids
                if sentence_id not in exact_sentence_ids
            )
        cannot_accept_maximum = selector._cannot_accept_maximum_score_fields
        for sentence_id in candidate_ids:
            record = self._records_by_id[sentence_id]
            if cannot_accept_maximum(
                maximum_score=maximum_score,
                completed_sentence=record.original,
                source_text=record.source_path,
                offset=record.line_number,
            ):
                continue
            score = _match_and_score(normalized_query, record.normalized)
            if score is None:
                continue
            selector.add(
                AutoCompleteData(
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                    score=score,
                )
            )

        return selector.results()


@dataclass(frozen=True, slots=True)
class ExactPathMetrics:
    candidates: int = 0
    records_examined: int = 0
    substring_checks: int = 0
    confirmed: int = 0
    alphabetical_rejections: int = 0
    result_allocations: int = 0
    elapsed_ns: int = 0
