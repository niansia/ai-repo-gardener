from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import FileRecord
from .parsing import normalized_tokens, structural_tokens


@dataclass(frozen=True)
class Similarity:
    ast: float
    tokens: float
    symbols: float
    overall: float


def compare(left: FileRecord, right: FileRecord) -> Similarity:
    ast_score = _sequence(_structural(left), _structural(right))
    token_score = _sequence(_normalized(left), _normalized(right))
    union = left.symbols | right.symbols
    symbol_score = len(left.symbols & right.symbols) / len(union) if union else 0.0
    overall = 0.50 * ast_score + 0.25 * token_score + 0.25 * symbol_score
    return Similarity(
        ast=ast_score, tokens=token_score, symbols=symbol_score, overall=overall
    )


def _structural(record: FileRecord) -> tuple[str, ...]:
    if not record.structural_tokens:
        record.structural_tokens = structural_tokens(record.source)
    return record.structural_tokens


def _normalized(record: FileRecord) -> tuple[str, ...]:
    if not record.normalized_tokens:
        record.normalized_tokens = normalized_tokens(record.source)
    return record.normalized_tokens


def _sequence(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio()
