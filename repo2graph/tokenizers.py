"""Token counters for pack budgets, each saying how exact it is (#290).

A pack's `token_count_method` names the counter that filled it:

- `heuristic` -- 4 characters per token, the default. Fine for English prose,
  low for dense code and CJK, so a pack can run past a model's real limit.
- `estimate:conservative-3cpt` -- 3 characters per token. Over-counts most
  text, so a pack filled with it stays under a limit the heuristic would cross.
- `exact:tiktoken/<encoding>` -- the real count for that BPE encoding, via the
  optional `tiktoken` package. Exact for the OpenAI models that use the
  encoding; for other providers it is a close estimate, not their tokenizer.

`tiktoken` downloads an encoding's BPE table on first use (cached afterwards,
`TIKTOKEN_CACHE_DIR` to pin where), so it is never the default.
"""

from __future__ import annotations

from typing import Any, Callable

from .query import count_tokens as heuristic

DEFAULT_TIKTOKEN_ENCODING = "o200k_base"
TOKENIZERS = ("heuristic", "conservative", "tiktoken")


def conservative(text: str) -> int:
    """3 characters per token, rounded up; never 0 for a non-empty string."""
    return -(-len(text) // 3) if text else 0


conservative.token_count_method = "estimate:conservative-3cpt"  # type: ignore[attr-defined]


def _tiktoken(encoding: str) -> Callable[[str], int]:
    try:
        import tiktoken
    except ImportError:
        raise ValueError(
            "--tokenizer tiktoken needs the tiktoken package: pip install tiktoken"
        ) from None
    try:
        enc: Any = tiktoken.get_encoding(encoding)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown tiktoken encoding {encoding!r}: {exc}") from None

    def count(text: str) -> int:
        return len(enc.encode(text, disallowed_special=())) if text else 0

    count.token_count_method = f"exact:tiktoken/{encoding}"  # type: ignore[attr-defined]
    return count


def get_token_counter(spec: str | None) -> Callable[[str], int]:
    """`heuristic` (or None), `conservative`, `tiktoken` or `tiktoken:<encoding>`.

    Raises:
        ValueError: An unknown name, or tiktoken asked for and unavailable.
    """
    name, _, arg = (spec or "heuristic").partition(":")
    if name == "heuristic" and not arg:
        return heuristic
    if name == "conservative" and not arg:
        return conservative
    if name == "tiktoken":
        return _tiktoken(arg or DEFAULT_TIKTOKEN_ENCODING)
    raise ValueError(f"unknown tokenizer {spec!r}; expected one of {', '.join(TOKENIZERS)}")
