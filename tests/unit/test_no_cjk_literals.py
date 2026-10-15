from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path


def _docstring_nodes(tree: ast.AST) -> set[tuple[int, int]]:
    result = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            result.add((body[0].value.lineno, body[0].value.col_offset))
    return result


def test_source_has_no_cjk_string_literals_outside_docstrings():
    for path in Path("src/travel_mode").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        docs = _docstring_nodes(ast.parse(source))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.STRING or (token.start[0], token.start[1]) in docs:
                continue
            assert not re.search(r"[\u4e00-\u9fff]", token.string), f"{path}:{token.start[0]}: {token.string}"
