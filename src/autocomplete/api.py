from typing import List  # noqa: UP035

from autocomplete.models import AutoCompleteData
from autocomplete.normalization import normalize
from autocomplete.query_cache import (
    DEFAULT_QUERY_CACHE_CAPACITY,
    QueryCacheInfo,
    QueryResultCache,
)
from autocomplete.search_engine import SearchEngine


class EngineNotInitializedError(RuntimeError):
    pass


_default_engine: SearchEngine | None = None
_query_cache = QueryResultCache()


def configure_default_engine(
    engine: SearchEngine,
    *,
    query_cache_capacity: int = DEFAULT_QUERY_CACHE_CAPACITY,
) -> None:
    global _default_engine, _query_cache

    _default_engine = engine
    _query_cache = QueryResultCache(query_cache_capacity)


def get_query_cache_info() -> QueryCacheInfo:
    """Return current autocomplete query-cache statistics."""
    return _query_cache.info()


def get_best_k_completions(prefix: str) -> List[AutoCompleteData]:  # noqa: UP006
    if _default_engine is None:
        raise EngineNotInitializedError(
            "The default SearchEngine has not been configured."
        )

    normalized_prefix = normalize(prefix)
    if not normalized_prefix:
        return _default_engine.search(prefix, k=5)

    cached_completions = _query_cache.get(normalized_prefix)
    if cached_completions is not None:
        return cached_completions

    completions = _default_engine.search(prefix, k=5)
    _query_cache.put(normalized_prefix, completions)
    return completions
