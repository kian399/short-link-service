import pytest

from app.services.shortener import BASE62_ALPHABET, base62_encode, generate_code


def test_base62_encode_zero():
    assert base62_encode(0) == "0"


def test_base62_encode_62():
    assert base62_encode(62) == "10"


def test_base62_encode_negative_rejected():
    with pytest.raises(ValueError):
        base62_encode(-1)


def test_generate_code_default_length():
    assert len(generate_code()) == 7


def test_generate_code_custom_length():
    assert len(generate_code(12)) == 12


def test_generate_code_charset():
    for char in generate_code(64):
        assert char in BASE62_ALPHABET


def test_generate_code_uniqueness():
    codes = {generate_code() for _ in range(1000)}
    assert len(codes) == 1000


def test_generate_code_rejects_bad_length():
    with pytest.raises(ValueError):
        generate_code(0)