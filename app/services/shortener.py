import hashlib

# Base62字符集：0-9, a-z, A-Z
BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(BASE62_ALPHABET)


def base62_encode(num: int) -> str:
    """把数字编码成Base62字符串"""
    if num == 0:
        return BASE62_ALPHABET[0]
    digits = []
    while num > 0:
        num, rem = divmod(num, BASE)
        digits.append(BASE62_ALPHABET[rem])
    return "".join(reversed(digits))


def obfuscate(seq_id: int) -> int:
    """用简单哈希混淆自增ID，避免连续可猜测"""
    # 简单混淆：乘一个大质数再左移几位，保证长度
    MIX_PRIME = 97
    return (seq_id * MIX_PRIME) << 8


def make_code(seq_id: int) -> str:
    """从序列ID生成6位左右的短码"""
    obfuscated = obfuscate(seq_id)
    code = base62_encode(obfuscated)
    # 前面补0到至少4位
    return code.rjust(4, "0")
