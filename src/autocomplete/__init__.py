"""FOX autocomplete snapshot runtime."""

from .index import SearchIndex
from .models import SentenceRecord
from .snapshot_loader import SnapshotError, load_snapshot

__all__ = ["SearchIndex", "SentenceRecord", "SnapshotError", "load_snapshot"]
