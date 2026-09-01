from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from autocomplete.matcher import match_and_score as _match_all_and_score
from autocomplete.matcher import match_non_exact_and_score as _match_and_score
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.observability import event, get_config, safe_reason, short_id
from autocomplete.ranking import TopKSelector
from autocomplete.scoring import exact_score as _exact_score

if TYPE_CHECKING:
    from autocomplete.index import SearchIndex


def _normalize(prefix: str) -> str:
    from autocomplete.normalization import normalize

    return normalize(prefix)


@dataclass
class _SearchObservation:
    stage: str = "input_validation"
    candidates: int = 0
    candidates_examined: int = 0
    exact_checks: int = 0
    exact_accepted: int = 0
    bound_checks: int = 0
    pruned: int = 0
    matcher_calls: int = 0
    matcher_valid: int = 0
    matcher_rejected: int = 0
    normalization_ns: int = 0
    iterator_creation_ns: int = 0
    iteration_ns: int = 0
    lookup_ns: int = 0
    exact_check_ns: int = 0
    bound_check_ns: int = 0
    construction_ns: int = 0
    ranking_ns: int = 0
    matcher_samples: list[int] = field(default_factory=list)

    def timed(self, detailed: bool) -> int:
        return time.perf_counter_ns() if detailed else 0

    @staticmethod
    def elapsed(started: int, detailed: bool) -> int:
        return time.perf_counter_ns() - started if detailed else 0


class SearchEngine:
    def __init__(
        self,
        records_by_id: dict[int, SentenceRecord],
        index: SearchIndex,
    ):
        self._records_by_id = records_by_id
        self._index = index

    def search(
        self,
        prefix: str,
        k: int = 5,
    ) -> list[AutoCompleteData]:
        config = get_config()
        if not config.enables(logging.CRITICAL):
            return self._search_fast(prefix, k)

        compute_started = time.perf_counter_ns()
        observation = _SearchObservation()
        common = {
            "request_id": short_id(),
            "raw_query_length": len(prefix) if isinstance(prefix, str) else 0,
        }
        if config.query_text and isinstance(prefix, str):
            common["query_text"] = prefix
        try:
            return self._search_observed(
                prefix,
                k,
                compute_started,
                common,
                observation,
                config.detailed_profiling,
            )
        except Exception as error:
            event(
                "runtime",
                "search.failed",
                logging.ERROR,
                **common,
                failed_stage=observation.stage,
                error_category=type(error).__name__,
                reason_code=safe_reason(error),
                candidate_count=observation.candidates,
                candidates_examined=observation.candidates_examined,
                search_compute_ms=(time.perf_counter_ns() - compute_started)
                / 1_000_000,
                status="failed",
            )
            raise

    def _search_fast(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
        """The origin/main search path, with no observability work."""

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
        if len(normalized_query) <= 3:
            exact_candidates = self._index.get_exact_candidate_ids(normalized_query)
            add_exact = selector._add_equal_score_fields
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
            score = _match_all_and_score(normalized_query, record.normalized)
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

    def _search_observed(
        self,
        prefix: str,
        k: int,
        compute_started: int,
        common: dict[str, object],
        observation: _SearchObservation,
        detailed: bool,
    ) -> list[AutoCompleteData]:
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("k must be an integer")
        if k < 0:
            raise ValueError("k must be non-negative")

        observation.stage = "normalization"
        started = observation.timed(detailed)
        normalized_query = "" if k == 0 else _normalize(prefix)
        observation.normalization_ns = observation.elapsed(started, detailed)
        if k == 0 or normalized_query == "":
            results: list[AutoCompleteData] = []
            self._log_completed(
                common,
                normalized_query,
                results,
                compute_started,
                observation,
                detailed,
            )
            return results

        selector = TopKSelector(k)
        maximum_score = _exact_score(len(normalized_query))
        exact_sentence_ids: set[int] = set()
        if len(normalized_query) <= 3:
            observation.stage = "exact_candidate_retrieval"
            started = observation.timed(detailed)
            exact_candidates = self._index.get_exact_candidate_ids(normalized_query)
            observation.iterator_creation_ns += observation.elapsed(started, detailed)
            observation.candidates += len(exact_candidates)
            observation.exact_accepted += len(exact_candidates)
            add_exact = selector._add_equal_score_fields
            for sentence_id in exact_candidates:
                observation.stage = "candidate_lookup"
                started = observation.timed(detailed)
                record = self._records_by_id[sentence_id]
                observation.lookup_ns += observation.elapsed(started, detailed)
                observation.candidates_examined += 1
                observation.stage = "result_construction"
                started = observation.timed(detailed)
                add_exact(
                    score=maximum_score,
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                )
                observation.construction_ns += observation.elapsed(started, detailed)
            if len(exact_candidates) >= k:
                results = self._rank(selector, observation, detailed)
                self._log_completed(
                    common,
                    normalized_query,
                    results,
                    compute_started,
                    observation,
                    detailed,
                )
                return results
            exact_sentence_ids.update(exact_candidates)

        observation.stage = "candidate_iterator_creation"
        started = observation.timed(detailed)
        candidate_ids = self._index.iter_candidate_ids(normalized_query)
        if exact_sentence_ids:
            candidate_ids = (
                sentence_id
                for sentence_id in candidate_ids
                if sentence_id not in exact_sentence_ids
            )
        iterator = iter(candidate_ids)
        observation.iterator_creation_ns += observation.elapsed(started, detailed)
        cannot_accept_maximum = selector._cannot_accept_maximum_score_fields
        while True:
            observation.stage = "candidate_iteration"
            started = observation.timed(detailed)
            try:
                sentence_id = next(iterator)
            except StopIteration:
                observation.iteration_ns += observation.elapsed(started, detailed)
                break
            observation.iteration_ns += observation.elapsed(started, detailed)
            observation.candidates += 1

            observation.stage = "candidate_lookup"
            started = observation.timed(detailed)
            record = self._records_by_id[sentence_id]
            observation.lookup_ns += observation.elapsed(started, detailed)
            observation.candidates_examined += 1

            observation.stage = "ranking_bound_check"
            started = observation.timed(detailed)
            cannot_accept = cannot_accept_maximum(
                maximum_score=maximum_score,
                completed_sentence=record.original,
                source_text=record.source_path,
                offset=record.line_number,
            )
            observation.bound_check_ns += observation.elapsed(started, detailed)
            observation.bound_checks += 1
            if cannot_accept:
                observation.pruned += 1
                continue

            observation.stage = "exact_match_check"
            started = observation.timed(detailed)
            is_exact = normalized_query in record.normalized
            observation.exact_check_ns += observation.elapsed(started, detailed)
            observation.exact_checks += 1
            if is_exact:
                observation.exact_accepted += 1
                score = maximum_score
            else:
                observation.stage = "matcher"
                sample_started = (
                    time.perf_counter_ns()
                    if detailed and len(observation.matcher_samples) < 1024
                    else 0
                )
                score = _match_and_score(normalized_query, record.normalized)
                if sample_started:
                    observation.matcher_samples.append(
                        time.perf_counter_ns() - sample_started
                    )
                observation.matcher_calls += 1
                if score is None:
                    observation.matcher_rejected += 1
                    continue
                observation.matcher_valid += 1

            observation.stage = "result_construction"
            started = observation.timed(detailed)
            selector.add(
                AutoCompleteData(
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                    score=score,
                )
            )
            observation.construction_ns += observation.elapsed(started, detailed)

        results = self._rank(selector, observation, detailed)
        self._log_completed(
            common,
            normalized_query,
            results,
            compute_started,
            observation,
            detailed,
        )
        return results

    @staticmethod
    def _rank(
        selector: TopKSelector,
        observation: _SearchObservation,
        detailed: bool,
    ) -> list[AutoCompleteData]:
        observation.stage = "ranking"
        started = observation.timed(detailed)
        results = selector.results()
        observation.ranking_ns += observation.elapsed(started, detailed)
        return results

    @staticmethod
    def _log_completed(
        common: dict[str, object],
        normalized_query: str,
        results: list[AutoCompleteData],
        compute_started: int,
        observation: _SearchObservation,
        detailed: bool,
    ) -> None:
        compute_ns = time.perf_counter_ns() - compute_started
        fields = dict(common)
        fields.update(
            normalized_query_length=len(normalized_query),
            candidates=observation.candidates,
            candidate_id_payload_bytes=observation.candidates * 4,
            candidates_examined=observation.candidates_examined,
            exact_checks=observation.exact_checks,
            exact_matches_accepted=observation.exact_accepted,
            top_k_bound_checks=observation.bound_checks,
            pruned_candidates=observation.pruned,
            candidates_checked=observation.exact_accepted + observation.matcher_calls,
            matcher_calls=observation.matcher_calls,
            matcher_valid=observation.matcher_valid,
            matcher_rejected=observation.matcher_rejected,
            valid_matches=observation.exact_accepted + observation.matcher_valid,
            rejected_candidates=observation.matcher_rejected,
            results_returned=len(results),
            search_compute_ms=compute_ns / 1_000_000,
            detailed_profiling=detailed,
            status="success",
        )
        if detailed:
            measured_ns = (
                observation.normalization_ns
                + observation.iterator_creation_ns
                + observation.iteration_ns
                + observation.lookup_ns
                + observation.exact_check_ns
                + observation.bound_check_ns
                + observation.construction_ns
                + observation.ranking_ns
                + sum(observation.matcher_samples)
            )
            samples = sorted(observation.matcher_samples)

            def percentile(fraction: float) -> float:
                if not samples:
                    return 0.0
                return samples[round((len(samples) - 1) * fraction)] / 1000

            fields.update(
                normalization_ms=observation.normalization_ns / 1_000_000,
                candidate_iterator_creation_ms=observation.iterator_creation_ns
                / 1_000_000,
                candidate_iteration_ms=observation.iteration_ns / 1_000_000,
                candidate_retrieval_ms=(
                    observation.iterator_creation_ns + observation.iteration_ns
                )
                / 1_000_000,
                candidate_lookup_ms=observation.lookup_ns / 1_000_000,
                exact_match_check_ms=observation.exact_check_ns / 1_000_000,
                top_k_bound_check_ms=observation.bound_check_ns / 1_000_000,
                result_construction_ms=observation.construction_ns / 1_000_000,
                ranking_ms=observation.ranking_ns / 1_000_000,
                matcher_sample_count=len(samples),
                matcher_sample_total_ms=sum(samples) / 1_000_000,
                matcher_sample_min_us=samples[0] / 1000 if samples else 0.0,
                matcher_sample_mean_us=(sum(samples) / len(samples) / 1000)
                if samples
                else 0.0,
                matcher_sample_p50_us=percentile(0.50),
                matcher_sample_p95_us=percentile(0.95),
                matcher_sample_p99_us=percentile(0.99),
                matcher_sample_max_us=samples[-1] / 1000 if samples else 0.0,
                profile_unaccounted_ms=max(0, compute_ns - measured_ns) / 1_000_000,
            )
        event("runtime", "search.completed", **fields)
