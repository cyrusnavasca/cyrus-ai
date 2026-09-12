"""Tiny JSONL read/write helpers. Stdlib only."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: bad JSON: {exc}") from exc


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
