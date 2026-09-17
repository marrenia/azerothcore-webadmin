"""Static audit: every route in every blueprint must carry an explicit
@require_role(...) decorator - not merely rely on a blueprint-wide before_request
hook, which is easy to forget to apply to a new blueprint.

This walks the actual AST of each bp_*.py file, so it can't be fooled by
comments or accidentally satisfied by a route defined with different
formatting. It is intentionally independent of the running Flask app.
"""
import ast
import glob
import os

import pytest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
BLUEPRINT_FILES = sorted(glob.glob(os.path.join(APP_DIR, "bp_*.py")))


def _decorator_names(func_def):
    names = []
    for dec in func_def.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
    return names


def _routes_in_file(path):
    """Yield (function_name, decorator_names) for every @bp.route(...) view."""
    tree = ast.parse(open(path).read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = _decorator_names(node)
        if "route" in names:
            yield node.name, names


@pytest.mark.parametrize("path", BLUEPRINT_FILES, ids=[os.path.basename(p) for p in BLUEPRINT_FILES])
def test_every_route_has_require_role(path):
    routes = list(_routes_in_file(path))
    assert routes, f"{path} defines no @bp.route views - unexpected, check the parser"
    missing = [name for name, decs in routes if "require_role" not in decs]
    assert not missing, (
        f"{os.path.basename(path)}: routes with no @require_role decorator: {missing}"
    )


def test_total_route_count_is_stable():
    """Sanity check the audit is actually seeing routes, not silently matching zero."""
    total = sum(len(list(_routes_in_file(p))) for p in BLUEPRINT_FILES)
    assert total >= 70, f"expected at least 70 routes across all blueprints, saw {total}"
