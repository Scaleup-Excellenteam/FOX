from autocomplete.generated import autocomplete_search_pb2 as search_pb2
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.protobuf_search_adapter import SearchProtobufAdapter


class RecordingEngine:
    def __init__(self, results: list[AutoCompleteData]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
        self.calls.append((query, k))
        return self.results[:k]


def _record() -> SentenceRecord:
    return SentenceRecord(9, "Original!", "original", "nested/a.txt", 12)


def test_adapter_calls_search_engine_and_preserves_final_result() -> None:
    result = AutoCompleteData("Original!", "nested/a.txt", 12, -3)
    engine = RecordingEngine([result])
    adapter = SearchProtobufAdapter(engine, {9: _record()})  # type: ignore[arg-type]

    response = adapter.handle(
        search_pb2.SearchRequest(protocol_version=1, query="RAW, Query", max_results=1)
    )

    assert engine.calls == [("RAW, Query", 1)]
    assert response.WhichOneof("outcome") == "success"
    assert list(response.success.results) == [
        search_pb2.SearchResult(
            completed_sentence="Original!",
            score=-3,
            source_text="nested/a.txt",
            offset=12,
            sentence_id=9,
        )
    ]


def test_zero_limit_uses_part_a_default_and_empty_results_are_success() -> None:
    engine = RecordingEngine([])
    response = SearchProtobufAdapter(engine, {9: _record()}).handle(  # type: ignore[arg-type]
        search_pb2.SearchRequest(protocol_version=1, query="")
    )
    assert engine.calls == [("", 5)]
    assert response.WhichOneof("outcome") == "success"
    assert response.success.results == []


def test_invalid_limit_and_version_are_structured_errors() -> None:
    engine = RecordingEngine([])
    adapter = SearchProtobufAdapter(engine, {9: _record()})  # type: ignore[arg-type]
    bad_limit = adapter.handle(
        search_pb2.SearchRequest(protocol_version=1, query="x", max_results=6)
    )
    bad_version = adapter.handle(
        search_pb2.SearchRequest(protocol_version=2, query="x")
    )
    assert bad_limit.WhichOneof("outcome") == "error"
    assert bad_version.WhichOneof("outcome") == "error"
    assert bad_limit.error.code == search_pb2.SEARCH_ERROR_CODE_INVALID_REQUEST
    assert bad_version.error.code == search_pb2.SEARCH_ERROR_CODE_UNSUPPORTED_VERSION
    assert engine.calls == []
