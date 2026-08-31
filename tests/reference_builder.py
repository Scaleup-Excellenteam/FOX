#!/usr/bin/env python3
"""Test-only reference writer for the frozen Phase 0 snapshot contract."""

import hashlib
import string
import struct
import sys
from pathlib import Path

from autocomplete.generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    SentenceRecordProto,
    SnapshotManifestProto,
)

PUNCTUATION = str.maketrans("", "", string.punctuation)


def normalize(value):
    return " ".join(value.lower().translate(PUNCTUATION).split())


def framed(messages):
    output = bytearray()
    for message in messages:
        payload = message.SerializeToString(deterministic=True)
        output.extend(struct.pack(">I", len(payload)))
        output.extend(payload)
    return bytes(output)


def update_string(digest, value):
    encoded = value.encode()
    digest.update(struct.pack(">Q", len(encoded)))
    digest.update(encoded)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--normalize":
        print(normalize(sys.argv[2]), end="")
        return 0
    if len(sys.argv) not in (3, 4):
        print(
            "usage: fox_snapshot_builder CORPUS OUTPUT [SHARD_BYTES]", file=sys.stderr
        )
        return 2
    corpus, output = map(Path, sys.argv[1:3])
    if not corpus.is_dir():
        print("corpus root is not a directory", file=sys.stderr)
        return 1
    if output.exists():
        print("output already exists", file=sys.stderr)
        return 1

    records = []
    corpus_digest = hashlib.sha256()
    for source in sorted(
        path
        for path in corpus.rglob("*")
        if path.is_file() and path.suffix.lower() == ".txt"
    ):
        try:
            lines = source.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            print(f"invalid UTF-8: {source}", file=sys.stderr)
            return 1
        relative = source.relative_to(corpus).as_posix()
        for line_number, original in enumerate(lines, 1):
            normalized = normalize(original)
            if not normalized:
                continue
            record = SentenceRecordProto(
                sentence_id=len(records) + 1,
                original=original,
                normalized=normalized,
                source_path=relative,
                line_number=line_number,
            )
            records.append(record)
            corpus_digest.update(struct.pack(">Q", record.sentence_id))
            update_string(corpus_digest, relative)
            corpus_digest.update(struct.pack(">Q", line_number))
            update_string(corpus_digest, original)
            update_string(corpus_digest, normalized)

    posting_ids = {}
    for record in records:
        for size in (1, 2, 3):
            for offset in range(len(record.normalized) - size + 1):
                key = (size, record.normalized[offset : offset + size])
                posting_ids.setdefault(key, set()).add(record.sentence_id)
    postings = [
        GramPostingProto(gram_size=size, gram=gram, sentence_ids=sorted(ids))
        for (size, gram), ids in sorted(
            posting_ids.items(), key=lambda item: (item[0][0], item[0][1].encode())
        )
    ]
    index_digest = hashlib.sha256()
    for posting in postings:
        index_digest.update(struct.pack(">I", posting.gram_size))
        update_string(index_digest, posting.gram)
        index_digest.update(struct.pack(">Q", len(posting.sentence_ids)))
        for sentence_id in posting.sentence_ids:
            index_digest.update(struct.pack(">Q", sentence_id))

    corpus_hex = corpus_digest.hexdigest()
    index_hex = index_digest.hexdigest()
    identity = (
        f"corpus_digest_sha256={corpus_hex}\n"
        f"index_digest_sha256={index_hex}\n"
        "schema_version=1\nnormalization_version=1\n"
        "index_strategy_version=1\ngram_sizes=1,2,3\n"
    )
    manifest = SnapshotManifestProto(
        schema_version=1,
        normalization_version=1,
        index_strategy_version=1,
        gram_sizes=[1, 2, 3],
        corpus_digest_sha256=corpus_hex,
        snapshot_id=hashlib.sha256(identity.encode()).hexdigest(),
        created_at_utc="1970-01-01T00:00:00Z",
        record_files=["records.binpb"],
        index_files=["index.binpb"],
        searchable_record_count=len(records),
        posting_count=len(postings),
        index_digest_sha256=index_hex,
    )
    output.mkdir()
    (output / "records.binpb").write_bytes(framed(records))
    (output / "index.binpb").write_bytes(framed(postings))
    (output / "manifest.binpb").write_bytes(
        manifest.SerializeToString(deterministic=True)
    )
    print(f"sentences={len(records)} files={len(list(corpus.rglob('*.txt')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
