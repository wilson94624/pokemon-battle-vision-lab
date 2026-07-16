"""OCR 文字的輕量正規化與字元統計；不做字典式或語意校正。"""

import re
import unicodedata
from typing import List


_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)


def is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def cjk_character_count(text: str) -> int:
    return sum(1 for character in text if is_cjk(character))


def normalize_ocr_text(raw_text: str) -> str:
    value = unicodedata.normalize("NFKC", raw_text or "")
    lines: List[str] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u3000]+", "", raw_line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def line_count(text: str) -> int:
    return len([line for line in (text or "").splitlines() if line.strip()])


def comparable_text(text: str) -> str:
    """相似度比較時忽略換行與常見標點，但保留實際 OCR 輸出。"""
    normalized = normalize_ocr_text(text)
    return "".join(
        character
        for character in normalized
        if character not in "\n，。！？、：；,.!?;:·・—-~～…「」『』（）()[]【】"
    )

