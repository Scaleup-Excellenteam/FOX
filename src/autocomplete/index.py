from __future__ import annotations

from array import array
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType


class PostingArray(array):
    """Compact sorted uint32 postings with sequence-compatible equality."""

    def __new__(cls, values: Iterable[int] = ()) -> PostingArray:
        return super().__new__(cls, "I", values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (tuple, list)):
            return len(self) == len(other) and all(left == right for left, right in zip(self, other))
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
            left == right for left, right in zip(self, other)
        )

    __hash__ = None


_EMPTY_POSTING = FrozenPosting()


def _intersect_sorted(left: Sequence[int], right: Sequence[int]) -> PostingArray:
    result = PostingArray()
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_id, right_id = left[left_index], right[right_index]
        if left_id == right_id:
            result.append(left_id)
            left_index += 1
            right_index += 1
        elif left_id < right_id:
            left_index += 1
        else:
            right_index += 1
    return result


def _union_sorted(left: Sequence[int], right: Sequence[int]) -> list[int]:
    result: list[int] = []
    left_index = right_index = 0
    while left_index < len(left) or right_index < len(right):
        if right_index >= len(right) or (
            left_index < len(left) and left[left_index] < right[right_index]
        ):
            value = left[left_index]
            left_index += 1
        elif left_index >= len(left) or right[right_index] < left[left_index]:
            value = right[right_index]
            right_index += 1
        else:
            value = left[left_index]
            left_index += 1
            right_index += 1
        result.append(value)
    return result


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
        instance._postings = {
            key: FrozenPosting(ids) for key, ids in postings.items()
        }
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
