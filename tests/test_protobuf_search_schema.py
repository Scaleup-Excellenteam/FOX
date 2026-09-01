from autocomplete.generated import autocomplete_search_pb2 as search_pb2


def test_search_schema_contract() -> None:
    assert search_pb2.DESCRIPTOR.package == "autocomplete.search.v1"
    request = search_pb2.SearchRequest.DESCRIPTOR
    assert [(field.name, field.number, field.type) for field in request.fields] == [
        ("protocol_version", 1, 13),
        ("query", 2, 9),
        ("max_results", 3, 13),
    ]
    result = search_pb2.SearchResult.DESCRIPTOR
    assert [(field.name, field.number, field.type) for field in result.fields] == [
        ("completed_sentence", 1, 9),
        ("score", 2, 17),
        ("source_text", 3, 9),
        ("offset", 4, 4),
        ("sentence_id", 5, 4),
    ]
    response = search_pb2.SearchResponse.DESCRIPTOR
    assert response.oneofs_by_name["outcome"].fields == [
        response.fields_by_name["success"],
        response.fields_by_name["error"],
    ]


def test_unicode_request_and_all_result_fields_round_trip() -> None:
    request = search_pb2.SearchRequest(
        protocol_version=1, query="Café, שלום", max_results=3
    )
    parsed_request = search_pb2.SearchRequest.FromString(request.SerializeToString())
    assert parsed_request == request

    response = search_pb2.SearchResponse(protocol_version=1)
    response.success.results.add(
        completed_sentence="Café שלום!",
        score=-5,
        source_text="nested/κόσμος.txt",
        offset=42,
        sentence_id=7,
    )
    parsed_response = search_pb2.SearchResponse.FromString(response.SerializeToString())
    assert parsed_response == response
    assert parsed_response.WhichOneof("outcome") == "success"


def test_success_and_error_are_mutually_exclusive() -> None:
    response = search_pb2.SearchResponse(protocol_version=1)
    response.success.SetInParent()
    response.error.code = search_pb2.SEARCH_ERROR_CODE_INTERNAL
    assert response.WhichOneof("outcome") == "error"
    assert not response.HasField("success")
