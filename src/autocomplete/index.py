from __future__ import annotations

from array import array
from collections.abc import Iterable, Iterator, Mapping, Sequence
from types import MappingProxyType


class PostingArray(array):
    """Compact sorted uint32 postings with sequence-compatible equality."""

    def __new__(cls, values: Iterable[int] = ()) -> PostingArray:
        return super().__new__(cls, "I", values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (tuple, list)):
            return len(self) == len(other) and all(
                left == right for left, right in zip(self, other, strict=True)
            )
        return bool(super().__eq__(other))

    __hash__ = None


class FrozenPosting(Sequence[int]):
    """Compact immutable uint32 postings.

    Values are stored as immutable bytes and viewed as native uint32 values. The
    one-time conversion at construction prevents mutation through input aliases;
    exposing this object (and a mapping proxy) is then O(1), unlike defensively
    copying every posting list whenever ``postings`` is accessed.
    """

    __slots__ = ("_storage", "_values")

    def __init__(self, values: Iterable[int] = ()) -> None:
        self._storage = PostingArray(values).tobytes()
        self._values = memoryview(self._storage).cast("I")

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int | slice) -> int | tuple[int, ...]:
        if isinstance(index, slice):
            return tuple(self._values[index])
        return self._values[index]

    def __iter__(self):
        return iter(self._values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return False
        return len(self) == len(other) and all(
            left == right for left, right in zip(self, other, strict=True)
        )

    __hash__ = None


_EMPTY_POSTING = FrozenPosting()


def _intersect_sorted(left: Sequence[int], right: Sequence[int]) -> PostingArray:
    result = PostingArray()
    left_iterator = iter(left)
    right_iterator = iter(right)
    try:
        left_id = next(left_iterator)
        right_id = next(right_iterator)
        while True:
            if left_id < right_id:
                left_id = next(left_iterator)
            elif right_id < left_id:
                right_id = next(right_iterator)
            else:
                result.append(left_id)
                left_id = next(left_iterator)
                right_id = next(right_iterator)
    except StopIteration:
        return result


def _union_sorted(left: Sequence[int], right: Sequence[int]) -> list[int]:
    result: list[int] = []
    append = result.append
    left_iterator = iter(left)
    right_iterator = iter(right)

    try:
        left_id = next(left_iterator)
    except StopIteration:
        return list(right_iterator)
    try:
        right_id = next(right_iterator)
    except StopIteration:
        return [left_id, *left_iterator]

    while True:
        if left_id < right_id:
            append(left_id)
            try:
                left_id = next(left_iterator)
            except StopIteration:
                append(right_id)
                result.extend(right_iterator)
                return result
        elif right_id < left_id:
            append(right_id)
            try:
                right_id = next(right_iterator)
            except StopIteration:
                append(left_id)
                result.extend(left_iterator)
                return result
        else:
            append(left_id)
            try:
                left_id = next(left_iterator)
            except StopIteration:
                result.extend(right_iterator)
                return result
            try:
                right_id = next(right_iterator)
            except StopIteration:
                append(left_id)
                result.extend(left_iterator)
                return result


def _iter_union_sorted(
    left: Sequence[int],
    right: Sequence[int],
) -> Iterator[int]:
    """Yield the sorted union without materializing the final candidate list."""

    left_iterator = iter(left)
    right_iterator = iter(right)
    try:
        left_id = next(left_iterator)
    except StopIteration:
        yield from right_iterator
        return
    try:
        right_id = next(right_iterator)
    except StopIteration:
        yield left_id
        yield from left_iterator
        return

    while True:
        if left_id < right_id:
            yield left_id
            try:
                left_id = next(left_iterator)
            except StopIteration:
                yield right_id
                yield from right_iterator
                return
        elif right_id < left_id:
            yield right_id
            try:
                right_id = next(right_iterator)
            except StopIteration:
                yield left_id
                yield from left_iterator
                return
        else:
            yield left_id
            try:
                left_id = next(left_iterator)
            except StopIteration:
                yield from right_iterator
                return
            try:
                right_id = next(right_iterator)
            except StopIteration:
                yield left_id
                yield from left_iterator
                return


class SearchIndex:
    """Immutable compact 1/2/3-character postings for recall-safe candidates."""

    __slots__ = ("_postings", "_postings_view", "_all_sentence_ids")

    def __init__(
        self,
        postings: Mapping[tuple[int, str], Iterable[int]],
        all_sentence_ids: Iterable[int],
    ) -> None:
        self._postings = {
            key: FrozenPosting(sorted(set(ids))) for key, ids in postings.items()
        }
        self._all_sentence_ids = FrozenPosting(sorted(set(all_sentence_ids)))
        self._postings_view = MappingProxyType(self._postings)

    @classmethod
    def _from_validated_postings(
        cls,
        postings: Mapping[tuple[int, str], Iterable[int]],
        all_sentence_ids: Iterable[int],
    ) -> SearchIndex:
        """Construct from loader-validated sorted, unique uint32 sequences."""
        instance = cls.__new__(cls)
        instance._postings = {key: FrozenPosting(ids) for key, ids in postings.items()}
        instance._all_sentence_ids = FrozenPosting(all_sentence_ids)
        instance._postings_view = MappingProxyType(instance._postings)
        return instance

    @property
    def postings(self) -> Mapping[tuple[int, str], FrozenPosting]:
        return self._postings_view

    @property
    def all_sentence_ids(self) -> FrozenPosting:
        return self._all_sentence_ids

    def _seed_candidates(self, seed: str) -> Sequence[int]:
        if len(seed) <= 2:
            return self._postings.get((len(seed), seed), _EMPTY_POSTING)
        keys = {(3, seed[index : index + 3]) for index in range(len(seed) - 2)}
        posting_lists = [self._postings.get(key, _EMPTY_POSTING) for key in keys]
        if not posting_lists or any(not posting for posting in posting_lists):
            return PostingArray()
        posting_lists.sort(key=len)
        if len(posting_lists) == 1:
            return posting_lists[0]
        result = PostingArray(posting_lists[0])
        for posting in posting_lists[1:]:
            result = _intersect_sorted(result, posting)
            if not result:
                break
        return result

    def get_candidate_ids(self, normalized_query: str) -> list[int]:
        if not isinstance(normalized_query, str):
            raise TypeError("normalized_query must be a string")
        if len(normalized_query) <= 1:
            return list(self._all_sentence_ids)
        split_at = len(normalized_query) // 2
        left = self._seed_candidates(normalized_query[:split_at])
        right = self._seed_candidates(normalized_query[split_at:])
        return _union_sorted(left, right)

    def iter_candidate_ids(self, normalized_query: str) -> Iterator[int]:
        """Yield fuzzy-safe candidates without materializing their final union."""

        if not isinstance(normalized_query, str):
            raise TypeError("normalized_query must be a string")
        if len(normalized_query) <= 1:
            return iter(self._all_sentence_ids)
        split_at = len(normalized_query) // 2
        left = self._seed_candidates(normalized_query[:split_at])
        right = self._seed_candidates(normalized_query[split_at:])
        return _iter_union_sorted(left, right)

    def get_exact_candidate_ids(self, normalized_query: str) -> Sequence[int]:
        """Return the direct exact posting for a 1/2/3-character query."""

        if not isinstance(normalized_query, str):
            raise TypeError("normalized_query must be a string")
        if not 1 <= len(normalized_query) <= 3:
            raise ValueError("exact candidate query length must be between 1 and 3")
        return self._postings.get(
            (len(normalized_query), normalized_query),
            _EMPTY_POSTING,
        )
