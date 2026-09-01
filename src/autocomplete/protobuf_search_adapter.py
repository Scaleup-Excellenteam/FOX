"""Conversions between the search Protobuf contract and Part A SearchEngine."""

from __future__ import annotations

from collections.abc import Mapping

from autocomplete.generated import autocomplete_search_pb2 as search_pb2
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.search_engine import SearchEngine

PROTOCOL_VERSION = 1
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS = 5


class SearchProtobufAdapter:
    """A thin protocol adapter; all search behavior remains in SearchEngine."""

    def __init__(
        self,
        engine: SearchEngine,
        records_by_id: Mapping[int, SentenceRecord],
    ) -> None:
        self._engine = engine
        self._sentence_ids = {
            (record.original, record.source_path, record.line_number): sentence_id
            for sentence_id, record in records_by_id.items()
        }

    def handle(self, request: search_pb2.SearchRequest) -> search_pb2.SearchResponse:
        validation_error = self._validate(request)
        if validation_error is not None:
            return validation_error

        limit = request.max_results or DEFAULT_MAX_RESULTS
        results = self._engine.search(request.query, k=limit)
        response = search_pb2.SearchResponse(protocol_version=PROTOCOL_VERSION)
        response.success.SetInParent()
        for result in results:
            response.success.results.append(self._convert_result(result))
        return response

    def _validate(
        self, request: search_pb2.SearchRequest
    ) -> search_pb2.SearchResponse | None:
        if request.protocol_version != PROTOCOL_VERSION:
            return error_response(
                search_pb2.SEARCH_ERROR_CODE_UNSUPPORTED_VERSION,
                f"unsupported protocol version: {request.protocol_version}",
            )
        if request.max_results > MAX_RESULTS:
            return error_response(
                search_pb2.SEARCH_ERROR_CODE_INVALID_REQUEST,
                f"max_results must be between 1 and {MAX_RESULTS}, or 0 for default",
            )
        return None

    def _convert_result(self, result: AutoCompleteData) -> search_pb2.SearchResult:
        key = (result.completed_sentence, result.source_text, result.offset)
        sentence_id = self._sentence_ids.get(key)
        if sentence_id is None:
            raise RuntimeError(
                "SearchEngine returned a result absent from its snapshot"
            )
        return search_pb2.SearchResult(
            completed_sentence=result.completed_sentence,
            score=result.score,
            source_text=result.source_text,
            offset=result.offset,
            sentence_id=sentence_id,
        )


def error_response(code: int, message: str) -> search_pb2.SearchResponse:
    return search_pb2.SearchResponse(
        protocol_version=PROTOCOL_VERSION,
        error=search_pb2.SearchError(code=code, message=message),
    )
