from dataclasses import dataclass


@dataclass(frozen=True)
class SentenceRecord:
    sentence_id: int
    original: str
    normalized: str
    source_path: str
    line_number: int


@dataclass
class AutoCompleteData:
    completed_sentence: str
    source_text: str
    offset: int
    score: int
