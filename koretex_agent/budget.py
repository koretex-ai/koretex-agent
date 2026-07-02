"""Prefix accounting. tiktoken(o200k_base) is a proxy tokenizer — real serving
uses the Qwen tokenizer, which counts slightly differently; budgets carry that
margin. What matters is that the number is measured the same way every build."""
from __future__ import annotations

import json

import tiktoken

from .tools import TOOL_SCHEMAS

_ENC = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def profile_prefix_tokens(profile) -> int:
    """The fixed cost paid on every call: system prompt + tool schemas."""
    n = count_tokens(profile.system_prompt())
    for tool in profile.tools:
        n += count_tokens(json.dumps(TOOL_SCHEMAS[tool], separators=(",", ":")))
    return n
