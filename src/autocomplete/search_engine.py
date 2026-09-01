from __future__ import annotations

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
    ):
        self._records_by_id = records_by_id
        self._index = index

    def search(
        self,
        prefix: str,
        k: int = 5,
    ) -> list[AutoCompleteData]:
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
