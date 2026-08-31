from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SentenceRecord:
    sentence_id: int
    original: str
    normalized: str
    source_path: str
    line_number: int
