"""繁中戰鬥訊息的保守正規化；不做字典校正或遊戲狀態推論。"""

import re
import unicodedata
from typing import List, Optional, Tuple


_TERMINAL_PUNCTUATION = "!！。.?？"


def normalize_battle_text(raw_text: str) -> str:
    """統一 Unicode／空白，但保留換行作為 OCR 版面證據。"""
    value = unicodedata.normalize("NFKC", raw_text or "")
    lines = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u3000]+", "", raw_line.strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def compact_battle_text(normalized_text: str) -> str:
    """規則比對使用無換行形式；輸出仍保留 normalized_text 的行界。"""
    return "".join(normalized_text.splitlines())


def strip_terminal_punctuation(value: str) -> str:
    return value.strip().rstrip(_TERMINAL_PUNCTUATION)


def parse_subjects(value: str) -> Tuple[List[str], Optional[str]]:
    """將「對手的A和B」拆成實體列表與視角；不猜測未明示的陣營。"""
    text = strip_terminal_punctuation(value)
    side: Optional[str] = None
    if text.startswith("對手的"):
        side = "opponent"
        text = text[len("對手的") :]
    names = []
    for part in re.split(r"[和、]", text):
        name = part
        if name.startswith("對手的"):
            side = side or "opponent"
            name = name[len("對手的") :]
        name = name.strip().rstrip("的")
        if name:
            names.append(name)
    return names, side


def parse_go_switch_targets(normalized_text: str) -> List[str]:
    """「上吧」雙打訊息用換行分隔兩隻寶可夢，需在 compact 前保留。"""
    lines = normalized_text.splitlines()
    if not lines:
        return []
    first = lines[0]
    if first.startswith("上吧!"):
        first = first[len("上吧!") :]
    elif first.startswith("上吧！"):
        first = first[len("上吧！") :]
    pieces = [first] + lines[1:]
    targets = []
    for piece in pieces:
        for name in re.split(r"[和、]", strip_terminal_punctuation(piece)):
            if name:
                targets.append(name)
    return targets
