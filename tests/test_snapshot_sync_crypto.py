import pytest
import tink

from autocomplete.snapshot_sync.crypto import (
    decrypt_chunk,
    encrypt_chunk,
    new_test_keyset,
    primitive,
)

MISSION = "MISSION-ALPHA"
SATELLITE = "SAT-07"
SNAPSHOT = "a" * 64


@pytest.fixture
def streaming_primitive():
    return primitive(new_test_keyset())


def test_chunk_round_trips_unchanged(streaming_primitive) -> None:
    plaintext = b"snapshot payload" * 1000
    ciphertext = encrypt_chunk(
        plaintext, streaming_primitive, MISSION, SATELLITE, SNAPSHOT
    )

    assert (
        decrypt_chunk(ciphertext, streaming_primitive, MISSION, SATELLITE, SNAPSHOT)
        == plaintext
    )


def test_flipped_ciphertext_byte_raises(streaming_primitive) -> None:
    ciphertext = bytearray(
        encrypt_chunk(b"payload", streaming_primitive, MISSION, SATELLITE, SNAPSHOT)
    )
    ciphertext[len(ciphertext) // 2] ^= 1

    with pytest.raises(tink.TinkError):
        decrypt_chunk(
            bytes(ciphertext), streaming_primitive, MISSION, SATELLITE, SNAPSHOT
        )


def test_wrong_key_raises(streaming_primitive) -> None:
    ciphertext = encrypt_chunk(
        b"payload", streaming_primitive, MISSION, SATELLITE, SNAPSHOT
    )

    with pytest.raises(tink.TinkError):
        decrypt_chunk(
            ciphertext,
            primitive(new_test_keyset()),
            MISSION,
            SATELLITE,
            SNAPSHOT,
        )


def test_wrong_satellite_associated_data_raises(streaming_primitive) -> None:
    ciphertext = encrypt_chunk(
        b"payload", streaming_primitive, MISSION, SATELLITE, SNAPSHOT
    )

    with pytest.raises(tink.TinkError):
        decrypt_chunk(ciphertext, streaming_primitive, MISSION, "SAT-01", SNAPSHOT)
