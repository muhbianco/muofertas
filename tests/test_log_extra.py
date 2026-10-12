"""Nenhum `extra=` de log usa nome reservado do LogRecord.

`logging` levanta KeyError ("Attempt to overwrite 'created' in LogRecord") só quando o nível está
ligado, ou seja, em produção. Foi assim que "mudar de grupo" virou 404 em 12/10/2026.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
RESERVED = set(logging.LogRecord("x", logging.INFO, "x", 1, "m", None, None).__dict__) | {
    "message",
    "asctime",
}


def test_no_log_extra_uses_a_reserved_name() -> None:
    found = []
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                    found += [
                        f"{path.relative_to(APP.parent)}:{key.lineno} {key.value!r}"
                        for key in kw.value.keys
                        if isinstance(key, ast.Constant) and key.value in RESERVED
                    ]
    assert not found, found
