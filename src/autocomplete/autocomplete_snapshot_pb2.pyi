from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor
SHARD_KIND_INDEX: ShardKind
SHARD_KIND_RECORDS: ShardKind
SHARD_KIND_UNSPECIFIED: ShardKind

class NGramIndexSpec(_message.Message):
    __slots__ = ["gram_codepoints", "min_selective_query_codepoints", "shard_target_bytes", "version"]
    GRAM_CODEPOINTS_FIELD_NUMBER: _ClassVar[int]
    MIN_SELECTIVE_QUERY_CODEPOINTS_FIELD_NUMBER: _ClassVar[int]
    SHARD_TARGET_BYTES_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    gram_codepoints: _containers.RepeatedScalarFieldContainer[int]
    min_selective_query_codepoints: int
    shard_target_bytes: int
    version: int
    def __init__(self, version: _Optional[int] = ..., gram_codepoints: _Optional[_Iterable[int]] = ..., min_selective_query_codepoints: _Optional[int] = ..., shard_target_bytes: _Optional[int] = ...) -> None: ...

class NormalizationSpec(_message.Message):
    __slots__ = ["algorithm", "version"]
    ALGORITHM_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    algorithm: str
    version: int
    def __init__(self, version: _Optional[int] = ..., algorithm: _Optional[str] = ...) -> None: ...

class PostingChunk(_message.Message):
    __slots__ = ["chunk_index", "gram", "gram_size", "is_last_chunk", "sentence_ids"]
    CHUNK_INDEX_FIELD_NUMBER: _ClassVar[int]
    GRAM_FIELD_NUMBER: _ClassVar[int]
    GRAM_SIZE_FIELD_NUMBER: _ClassVar[int]
    IS_LAST_CHUNK_FIELD_NUMBER: _ClassVar[int]
    SENTENCE_IDS_FIELD_NUMBER: _ClassVar[int]
    chunk_index: int
    gram: str
    gram_size: int
    is_last_chunk: bool
    sentence_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, gram_size: _Optional[int] = ..., gram: _Optional[str] = ..., chunk_index: _Optional[int] = ..., is_last_chunk: bool = ..., sentence_ids: _Optional[_Iterable[int]] = ...) -> None: ...

class SentenceRecord(_message.Message):
    __slots__ = ["normalized_text", "original_text", "sentence_id", "source_line_number", "source_relative_path"]
    NORMALIZED_TEXT_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_TEXT_FIELD_NUMBER: _ClassVar[int]
    SENTENCE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_LINE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    SOURCE_RELATIVE_PATH_FIELD_NUMBER: _ClassVar[int]
    normalized_text: str
    original_text: str
    sentence_id: int
    source_line_number: int
    source_relative_path: str
    def __init__(self, sentence_id: _Optional[int] = ..., source_relative_path: _Optional[str] = ..., source_line_number: _Optional[int] = ..., original_text: _Optional[str] = ..., normalized_text: _Optional[str] = ...) -> None: ...

class ShardMetadata(_message.Message):
    __slots__ = ["file_name", "first_gram", "first_gram_size", "frame_count", "framed_size_bytes", "kind", "last_gram", "last_gram_size", "sha256"]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    FIRST_GRAM_FIELD_NUMBER: _ClassVar[int]
    FIRST_GRAM_SIZE_FIELD_NUMBER: _ClassVar[int]
    FRAMED_SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    FRAME_COUNT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LAST_GRAM_FIELD_NUMBER: _ClassVar[int]
    LAST_GRAM_SIZE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    file_name: str
    first_gram: str
    first_gram_size: int
    frame_count: int
    framed_size_bytes: int
    kind: ShardKind
    last_gram: str
    last_gram_size: int
    sha256: bytes
    def __init__(self, file_name: _Optional[str] = ..., kind: _Optional[_Union[ShardKind, str]] = ..., framed_size_bytes: _Optional[int] = ..., frame_count: _Optional[int] = ..., sha256: _Optional[bytes] = ..., first_gram_size: _Optional[int] = ..., first_gram: _Optional[str] = ..., last_gram_size: _Optional[int] = ..., last_gram: _Optional[str] = ...) -> None: ...

class SnapshotManifest(_message.Message):
    __slots__ = ["corpus_digest", "created_at_utc", "framing_version", "index_digest", "index_shards", "ngram_index", "normalization", "record_shards", "schema_version", "sentence_count", "snapshot_id"]
    CORPUS_DIGEST_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_UTC_FIELD_NUMBER: _ClassVar[int]
    FRAMING_VERSION_FIELD_NUMBER: _ClassVar[int]
    INDEX_DIGEST_FIELD_NUMBER: _ClassVar[int]
    INDEX_SHARDS_FIELD_NUMBER: _ClassVar[int]
    NGRAM_INDEX_FIELD_NUMBER: _ClassVar[int]
    NORMALIZATION_FIELD_NUMBER: _ClassVar[int]
    RECORD_SHARDS_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    SENTENCE_COUNT_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_ID_FIELD_NUMBER: _ClassVar[int]
    corpus_digest: bytes
    created_at_utc: str
    framing_version: int
    index_digest: bytes
    index_shards: _containers.RepeatedCompositeFieldContainer[ShardMetadata]
    ngram_index: NGramIndexSpec
    normalization: NormalizationSpec
    record_shards: _containers.RepeatedCompositeFieldContainer[ShardMetadata]
    schema_version: int
    sentence_count: int
    snapshot_id: str
    def __init__(self, schema_version: _Optional[int] = ..., framing_version: _Optional[int] = ..., sentence_count: _Optional[int] = ..., normalization: _Optional[_Union[NormalizationSpec, _Mapping]] = ..., ngram_index: _Optional[_Union[NGramIndexSpec, _Mapping]] = ..., record_shards: _Optional[_Iterable[_Union[ShardMetadata, _Mapping]]] = ..., index_shards: _Optional[_Iterable[_Union[ShardMetadata, _Mapping]]] = ..., snapshot_id: _Optional[str] = ..., corpus_digest: _Optional[bytes] = ..., created_at_utc: _Optional[str] = ..., index_digest: _Optional[bytes] = ...) -> None: ...

class ShardKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
