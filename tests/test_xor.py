import pytest

from overlay.dht.xor import (
    bucket_index,
    common_prefix_length,
    xor_distance,
    xor_distance_int,
)


def hexid(s: str) -> bytes:
    return bytes.fromhex(s)


ID_ZERO = hexid("00" * 32)
ID_ONE = hexid("00" * 31 + "01")
ID_FF = hexid("ff" * 32)
ID_HIGH = hexid("80" + "00" * 31)


def test_xor_distance_zero_for_same_id():
    assert xor_distance(ID_ZERO, ID_ZERO) == b"\x00" * 32
    assert xor_distance_int(ID_ZERO, ID_ZERO) == 0


def test_xor_distance_max_for_opposite_ids():
    assert xor_distance(ID_ZERO, ID_FF) == b"\xff" * 32
    assert xor_distance_int(ID_ZERO, ID_FF) == (1 << 256) - 1


def test_xor_distance_between_adjacent_ids():
    assert xor_distance_int(ID_ZERO, ID_ONE) == 1


def test_xor_distance_is_symmetric():
    a = hexid("aa" * 32)
    b = hexid("55" * 32)
    assert xor_distance_int(a, b) == xor_distance_int(b, a)


def test_xor_distance_invalid_length():
    with pytest.raises(ValueError):
        xor_distance(b"\x00" * 16, b"\x00" * 32)


def test_common_prefix_length_identical():
    assert common_prefix_length(ID_ZERO, ID_ZERO) == 256


def test_common_prefix_length_first_bit_differs():
    assert common_prefix_length(ID_ZERO, ID_HIGH) == 0


def test_common_prefix_length_last_bit_differs():
    assert common_prefix_length(ID_ZERO, ID_ONE) == 255


def test_common_prefix_length_mid():
    b = hexid("00" * 16 + "01" + "00" * 15)
    assert common_prefix_length(ID_ZERO, b) == 135


def test_bucket_index_zero_for_high_bit():
    assert bucket_index(ID_ZERO, ID_HIGH) == 0


def test_bucket_index_max_for_last_bit():
    assert bucket_index(ID_ZERO, ID_ONE) == 255


def test_bucket_index_rejects_self():
    with pytest.raises(ValueError):
        bucket_index(ID_ZERO, ID_ZERO)