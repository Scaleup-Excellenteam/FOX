from __future__ import annotations

from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.ranking import rank_results


def _normalize(prefix: str) -> str:
    from autocomplete.keyboard_layout import convert_hebrew_keyboard_to_english
    from autocomplete.normalization import normalize

    return normalize(convert_hebrew_keyboard_to_english(prefix))


def _match_and_score(normalized_query: str, normalized_sentence: str) -> int | None:
    from autocomplete.matcher import match_and_score

    return match_and_score(normalized_query, normalized_sentence)


class ReferenceEngine:
    def __init__(
        self,
        records_by_id: dict[int, SentenceRecord],
    ):
        self._records_by_id = records_by_id

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

        results = []
        for record in self._records_by_id.values():
            score = _match_and_score(normalized_query, record.normalized)
            if score is None:
                continue
            results.append(
                AutoCompleteData(
                    completed_sentence=record.original,
                    source_text=record.source_path,
                    offset=record.line_number,
                    score=score,
                )
            )

        return rank_results(results)[:k]
