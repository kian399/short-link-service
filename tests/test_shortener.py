from app.services.shortener import base62_encode, make_code, BASE62_ALPHABET


def test_base62_encode_zero():
    assert base62_encode(0) == "0"


def test_base62_encode_62():
    assert base62_encode(62) == "10"


def test_make_code_length():
    code = make_code(1)
    assert 4 <= len(code) <= 8


def test_make_code_charset():
    code = make_code(12345)
    for c in code:
        assert c in BASE62_ALPHABET


def test_make_code_unique():
    codes = [make_code(i) for i in range(1, 1000)]
    assert len(set(codes)) == 999
