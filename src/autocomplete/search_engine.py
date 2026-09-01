from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

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
        request_id = short_id()
        total_started = time.perf_counter_ns()
        stage = "input_validation"
        candidate_count = 0
        config = get_config()
        common = {
            "request_id": request_id,
            "raw_query_length": len(prefix) if isinstance(prefix, str) else 0,
        }
        if config.query_text and isinstance(prefix, str):
            common["query_text"] = prefix
        try:
            return self._search_observed(
                prefix, k, total_started, common, config.detailed_profiling
            )
        except Exception as error:
            event(
                "runtime",
                "search.failed",
                logging.ERROR,
                **common,
                failed_stage=stage,
                error_category=type(error).__name__,
                reason=safe_reason(error),
                candidate_count=candidate_count,
                total_ms=(time.perf_counter_ns() - total_started) / 1_000_000,
                status="failed",
            )
            raise

    def _search_observed(
        self,
        prefix: str,
        k: int,
        total_started: int,
        common: dict[str, object],
        detailed: bool,
    ) -> list[AutoCompleteData]:
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("k must be an integer")
        if k < 0:
            raise ValueError("k must be non-negative")
        normalization_started = time.perf_counter_ns()
        if k == 0:
            normalized_query = ""
        else:
            normalized_query = _normalize(prefix)
        normalization_ns = time.perf_counter_ns() - normalization_started

        if k == 0 or normalized_query == "":
            event(
                "runtime",
                "search.completed",
                **common,
                normalized_query_length=len(normalized_query),
                normalization_ms=normalization_ns / 1_000_000,
                candidates=0,
                candidate_id_payload_bytes=0,
                candidates_examined=0,
                candidate_iterator_creation_ms=0.0,
                candidate_iteration_ms=0.0,
                candidate_retrieval_ms=0.0,
                candidate_lookup_ms=0.0,
                exact_match_check_ms=0.0,
                exact_checks=0,
                exact_matches_accepted=0,
                top_k_bound_check_ms=0.0,
                top_k_bound_checks=0,
                pruned_candidates=0,
                candidates_checked=0,
                matcher_calls=0,
                matcher_ms=0.0,
                average_candidate_us=0.0,
                matcher_valid=0,
                matcher_rejected=0,
                valid_matches=0,
                rejected_candidates=0,
                result_construction_ms=0.0,
                ranking_ms=0.0,
                results_returned=0,
                unaccounted_ms=0.0,
                total_ms=(time.perf_counter_ns() - total_started) / 1_000_000,
                status="success",
            )
            return []

        selector = TopKSelector(k)
        maximum_score = _exact_score(len(normalized_query))
        exact_sentence_ids: set[int] = set()
        candidate_count = candidates_examined = 0
        iterator_creation_ns = iteration_ns = lookup_ns = 0
        exact_check_ns = bound_check_ns = construction_ns = 0
        exact_checks = exact_accepted = bound_checks = pruned = 0
        matcher_ns = matcher_calls = matcher_valid = matcher_rejected = 0
        samples: list[int] = []
        if len(normalized_query) <= 3:
            started = time.perf_counter_ns()
            exact_candidates = self._index.get_exact_candidate_ids(normalized_query)
            iterator_creation_ns += time.perf_counter_ns() - started
            candidate_count += len(exact_candidates)
            candidates_examined += len(exact_candidates)
            exact_accepted += len(exact_candidates)
            add_exact = selector._add_equal_score_fields
            for sentence_id in exact_candidates:
                started = time.perf_counter_ns()
                record = self._records_by_id[sentence_id]
                lookup_ns += time.perf_counter_ns() - started
                started = time.perf_counter_ns()
                add_exact(
                    score=maximum_score,
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                )
                construction_ns += time.perf_counter_ns() - started
            if len(exact_candidates) >= k:
                started = time.perf_counter_ns()
                results = selector.results()
                ranking_ns = time.perf_counter_ns() - started
                self._log_completed(
                    common,
                    normalized_query,
                    normalization_ns,
                    candidate_count,
                    candidates_examined,
                    iterator_creation_ns,
                    iteration_ns,
                    lookup_ns,
                    exact_check_ns,
                    exact_checks,
                    exact_accepted,
                    bound_check_ns,
                    bound_checks,
                    pruned,
                    matcher_calls,
                    matcher_ns,
                    matcher_valid,
                    matcher_rejected,
                    construction_ns,
                    ranking_ns,
                    results,
                    total_started,
                    samples,
                )
                return results
            exact_sentence_ids.update(exact_candidates)

        # Candidate IDs are sentence-ID ordered, not rank ordered. The complete
        # stream must be scanned, but this global score bound can safely avoid the
        # matcher when even a maximum-scoring candidate cannot enter TOP K.
        started = time.perf_counter_ns()
        candidate_ids = self._index.iter_candidate_ids(normalized_query)
        if exact_sentence_ids:
            candidate_ids = (
                sentence_id
                for sentence_id in candidate_ids
                if sentence_id not in exact_sentence_ids
            )
        iterator = iter(candidate_ids)
        iterator_creation_ns += time.perf_counter_ns() - started
        cannot_accept_maximum = selector._cannot_accept_maximum_score_fields
        while True:
            started = time.perf_counter_ns()
            try:
                sentence_id = next(iterator)
            except StopIteration:
                iteration_ns += time.perf_counter_ns() - started
                break
            iteration_ns += time.perf_counter_ns() - started
            candidate_count += 1
            candidates_examined += 1
            started = time.perf_counter_ns()
            record = self._records_by_id[sentence_id]
            lookup_ns += time.perf_counter_ns() - started
            started = time.perf_counter_ns()
            cannot_accept = cannot_accept_maximum(
                maximum_score=maximum_score,
                completed_sentence=record.original,
                source_text=record.source_path,
                offset=record.line_number,
            )
            bound_check_ns += time.perf_counter_ns() - started
            bound_checks += 1
            if cannot_accept:
                pruned += 1
                continue
            started = time.perf_counter_ns()
            is_exact = normalized_query in record.normalized
            duration = time.perf_counter_ns() - started
            exact_check_ns += duration
            exact_checks += 1
            if is_exact:
                exact_accepted += 1
                started = time.perf_counter_ns()
                selector.add(
                    AutoCompleteData(
                        completed_sentence=record.original,
                        source_text=record.source_path,
                        offset=record.line_number,
                        score=maximum_score,
                    )
                )
                construction_ns += time.perf_counter_ns() - started
                continue
            started = time.perf_counter_ns()
            score = _match_and_score(normalized_query, record.normalized)
            duration = time.perf_counter_ns() - started
            matcher_ns += duration
            matcher_calls += 1
            if detailed and len(samples) < 1024:
                samples.append(duration)
            if score is None:
                matcher_rejected += 1
                continue
            matcher_valid += 1
            started = time.perf_counter_ns()
            selector.add(
                AutoCompleteData(
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                    score=score,
                )
            )
            construction_ns += time.perf_counter_ns() - started

        started = time.perf_counter_ns()
        results = selector.results()
        ranking_ns = time.perf_counter_ns() - started
        self._log_completed(
            common,
            normalized_query,
            normalization_ns,
            candidate_count,
            candidates_examined,
            iterator_creation_ns,
            iteration_ns,
            lookup_ns,
            exact_check_ns,
            exact_checks,
            exact_accepted,
            bound_check_ns,
            bound_checks,
            pruned,
            matcher_calls,
            matcher_ns,
            matcher_valid,
            matcher_rejected,
            construction_ns,
            ranking_ns,
            results,
            total_started,
            samples,
        )
        return results

    @staticmethod
    def _log_completed(
        common,
        normalized_query,
        normalization_ns,
        candidate_count,
        candidates_examined,
        iterator_creation_ns,
        iteration_ns,
        lookup_ns,
        exact_check_ns,
        exact_checks,
        exact_accepted,
        bound_check_ns,
        bound_checks,
        pruned,
        matcher_calls,
        matcher_ns,
        matcher_valid,
        matcher_rejected,
        construction_ns,
        ranking_ns,
        results,
        total_started,
        samples,
    ):
        before_observability_ns = time.perf_counter_ns()
        measured_ns = (
            normalization_ns
            + iterator_creation_ns
            + iteration_ns
            + lookup_ns
            + exact_check_ns
            + bound_check_ns
            + matcher_ns
            + construction_ns
            + ranking_ns
        )
        elapsed_ns = before_observability_ns - total_started
        fields = dict(common)
        fields.update(
            normalized_query_length=len(normalized_query),
            normalization_ms=normalization_ns / 1_000_000,
            candidates=candidate_count,
            candidate_id_payload_bytes=candidate_count * 4,
            candidates_examined=candidates_examined,
            candidate_iterator_creation_ms=iterator_creation_ns / 1_000_000,
            candidate_iteration_ms=iteration_ns / 1_000_000,
            candidate_retrieval_ms=(iterator_creation_ns + iteration_ns) / 1_000_000,
            candidate_lookup_ms=lookup_ns / 1_000_000,
            exact_match_check_ms=exact_check_ns / 1_000_000,
            exact_checks=exact_checks,
            exact_matches_accepted=exact_accepted,
            top_k_bound_check_ms=bound_check_ns / 1_000_000,
            top_k_bound_checks=bound_checks,
            pruned_candidates=pruned,
            candidates_checked=exact_accepted + matcher_calls,
            matcher_calls=matcher_calls,
            matcher_ms=matcher_ns / 1_000_000,
            average_candidate_us=(matcher_ns / matcher_calls / 1000)
            if matcher_calls
            else 0.0,
            matcher_valid=matcher_valid,
            matcher_rejected=matcher_rejected,
            valid_matches=exact_accepted + matcher_valid,
            rejected_candidates=matcher_rejected,
            result_construction_ms=construction_ns / 1_000_000,
            ranking_ms=ranking_ns / 1_000_000,
            results_returned=len(results),
            unaccounted_ms=max(0, elapsed_ns - measured_ns) / 1_000_000,
            total_ms=elapsed_ns / 1_000_000,
            status="success",
        )
        if samples:
            ordered = sorted(samples)

            def percentile(fraction: float) -> float:
                return ordered[round((len(ordered) - 1) * fraction)] / 1000

            fields.update(
                matcher_sample_count=len(samples),
                matcher_min_us=ordered[0] / 1000,
                matcher_mean_us=sum(ordered) / len(ordered) / 1000,
                matcher_p50_us=percentile(0.50),
                matcher_p95_us=percentile(0.95),
                matcher_p99_us=percentile(0.99),
                matcher_max_us=ordered[-1] / 1000,
            )
        event("runtime", "search.completed", **fields)
