"""A text file the owner may have saved from PowerShell 5.1. Standard library only.

UTF-8 reads, with or without the BOM `Out-File -Encoding utf8` writes. UTF-16,
which PowerShell 5.1's `>` writes, and anything else that is not UTF-8 are
refused by name. Line ends are left to the caller.
"""
from pathlib import Path

UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class NotUtf8(ValueError):
    """The file holds bytes this tool does not read; the message says what to do."""


def read_text(path):
    raw = Path(path).read_bytes()
    if raw[:2] in UTF16_BOMS:
        raise NotUtf8(f"{path} is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise NotUtf8(f"{path} is not UTF-8 (PowerShell 5.1's Set-Content writes ANSI): save it as UTF-8") from None
