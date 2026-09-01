from __future__ import annotations

from collections.abc import Iterable
from heapq import heappush, heapreplace
from operator import attrgetter

from autocomplete.models import AutoCompleteData


def rank_results(results: list[AutoCompleteData]) -> list[AutoCompleteData]:
    ranked = sorted(results, key=attrgetter("offset"))
    ranked.sort(key=attrgetter("source_text"))
    ranked.sort(key=attrgetter("completed_sentence"))
    ranked.sort(key=attrgetter("score"), reverse=True)
    return ranked


def _ranking_key(result: AutoCompleteData) -> tuple[int, str, str, int]:
    """Return the frozen total ordering used for autocomplete results."""

    return (
        -result.score,
        result.completed_sentence,
        result.source_text,
        result.offset,
    )


class _ReverseRankedItem:
    """Heap item whose smallest value is the worst retained ranking."""

    __slots__ = ("key", "order", "result")

    def __init__(
        self,
        key: tuple[int, str, str, int],
        order: int,
        result: AutoCompleteData,
    ) -> None:
        self.key = key
        self.order = order
        self.result = result

    def __lt__(self, other: _ReverseRankedItem) -> bool:
        return (self.key, self.order) > (other.key, other.order)


class TopKSelector:
    """Retain an exact, stable TOP K and expose its current admission bound."""

    __slots__ = ("_heap", "_k", "_next_order")

    def __init__(self, k: int) -> None:
        if k <= 0:
            raise ValueError("k must be positive")
        self._heap: list[_ReverseRankedItem] = []
        self._k = k
        self._next_order = 0

    def could_accept(
        self,
        *,
        score: int,
        completed_sentence: str,
        source_text: str,
        offset: int,
    ) -> bool:
        """Return whether a result with these fields could enter TOP K."""

        if len(self._heap) < self._k:
            return True
        key = (-score, completed_sentence, source_text, offset)
        return key < self._heap[0].key

    @property
    def worst_score(self) -> int | None:
        if len(self._heap) < self._k:
            return None
        return -self._heap[0].key[0]

    def add(self, result: AutoCompleteData) -> None:
        key = _ranking_key(result)
        item = _ReverseRankedItem(key, self._next_order, result)
        self._next_order += 1
        if len(self._heap) < self._k:
            heappush(self._heap, item)
            return
        if (key, item.order) < (self._heap[0].key, self._heap[0].order):
            heapreplace(self._heap, item)

    def _add_equal_score_fields(
        self,
        *,
        score: int,
        completed_sentence: str,
        source_text: str,
        offset: int,
    ) -> None:
        """Add fields through the equal-score hot path when they can enter TOP K.

        This internal path is for a stream whose candidates all have ``score``.
        Comparing primitive fields avoids allocating a ranking tuple for every
        rejected candidate while preserving the frozen lexicographic tie-break.
        """

        heap = self._heap
        if len(heap) >= self._k:
            worst = heap[0].result
            if completed_sentence > worst.completed_sentence:
                return
            if completed_sentence == worst.completed_sentence:
                if source_text > worst.source_text:
                    return
                if source_text == worst.source_text and offset >= worst.offset:
                    return

        result = AutoCompleteData(
            completed_sentence=completed_sentence,
            source_text=source_text,
            offset=offset,
            score=score,
        )
        key = (-score, completed_sentence, source_text, offset)
        item = _ReverseRankedItem(key, self._next_order, result)
        self._next_order += 1
        if len(heap) < self._k:
            heappush(heap, item)
        else:
            heapreplace(heap, item)

    def _cannot_accept_maximum_score_fields(
        self,
        *,
        maximum_score: int,
        completed_sentence: str,
        source_text: str,
        offset: int,
    ) -> bool:
        """Return whether the global score bound cannot enter the current TOP K."""

        heap = self._heap
        if len(heap) < self._k:
            return False
        worst = heap[0].result
        if worst.score != maximum_score:
            return False
        if completed_sentence > worst.completed_sentence:
            return True
        if completed_sentence < worst.completed_sentence:
            return False
        if source_text > worst.source_text:
            return True
        if source_text < worst.source_text:
            return False
        return offset >= worst.offset

    def results(self) -> list[AutoCompleteData]:
        ranked = sorted(self._heap, key=lambda item: (item.key, item.order))
        return [item.result for item in ranked]


def rank_top_k(
    results: Iterable[AutoCompleteData],
    k: int,
) -> list[AutoCompleteData]:
    """Return the exact best ``k`` results while retaining at most ``k`` items.

    The bounded heap consumes the complete iterable, so a strong result that
    appears late is still considered. An encounter-order field preserves Python's
    stable full-sort behavior for fully identical ranking keys.
    """

    if k <= 0:
        return []
    selector = TopKSelector(k)
    for result in results:
        selector.add(result)
    return selector.results()
