from typing import List  # noqa: UP035

from autocomplete.models import AutoCompleteData
from autocomplete.search_engine import SearchEngine


class EngineNotInitializedError(RuntimeError):
    pass


_default_engine: SearchEngine | None = None


def configure_default_engine(engine: SearchEngine) -> None:
    global _default_engine

    _default_engine = engine


def get_best_k_completions(prefix: str) -> List[AutoCompleteData]:  # noqa: UP006
    if _default_engine is None:
        raise EngineNotInitializedError(
            "The default SearchEngine has not been configured."
        )

    return _default_engine.search(prefix, k=5)
