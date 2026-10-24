import secrets

# Base62 字符集：0-9, a-z, A-Z
BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(BASE62_ALPHABET)
CODE_LENGTH = 7


def base62_encode(num: int) -> str:
    """把非负整数编码成 Base62 字符串（发号器方案的底层函数）。"""
    if num < 0:
        raise ValueError("num must be non-negative")
    if num == 0:
        return BASE62_ALPHABET[0]
    digits = []
    while num > 0:
        num, rem = divmod(num, BASE)
        digits.append(BASE62_ALPHABET[rem])
    return "".join(reversed(digits))


def generate_code(length: int = CODE_LENGTH) -> str:
    """用密码学安全随机数生成短码。

    62^7 约 3.5 万亿，随机生成实际不会碰撞；
    真正的并发兜底是数据库唯一索引 + 调用方重试。
    """
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(BASE62_ALPHABET) for _ in range(length))