#!/usr/bin/env python3
"""One-shot: move partner/*.py into layered packages and rewrite imports."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTNER = ROOT / "partner"
TESTS = ROOT / "tests"

LAYERS: dict[str, str] = {
    "lark": "core",
    "ids": "core",
    "session": "core",
    "events": "core",
    "ack": "core",
    "inbox": "core",
    "trace": "core",
    "run_store": "core",
    "intents": "routing",
    "mode_router": "routing",
    "resolved": "routing",
    "runner": "runtime",
    "planner": "runtime",
    "workflow": "runtime",
    "tool_registry": "runtime",
    "multi_agent": "runtime",
    "sandbox": "runtime",
    "artifact": "runtime",
    "brief": "office",
    "brief_card": "office",
    "followup": "office",
    "followup_card": "office",
    "bitable": "office",
    "schedule": "office",
    "watch": "office",
    "recap": "office",
    "report": "office",
    "memory": "office",
    "knowledge": "office",
    "rag": "office",
    "llm": "compose",
    "hermes_setup": "compose",
    "formatters": "compose",
    "cli": "ops",
    "serve": "ops",
    "setup": "ops",
    "eval": "ops",
    "versions": "ops",
    "aily": "ops",
    "webhook": "ops",
    "mcp_server": "ops",
    "mcp_http": "ops",
}

ROOT_STAY = {"actions", "paths", "__init__", "__main__"}


def layer_of(mod: str) -> str | None:
    return LAYERS.get(mod)


def rewrite_text(text: str, current_layer: str | None) -> str:
    """Rewrite partner-relative and partner.absolute imports for a file."""

    def rel_import(mod: str) -> str:
        target = layer_of(mod)
        if current_layer is None:
            # file at partner root (actions.py, paths.py, __main__)
            if target:
                return f".{target}.{mod}"
            return f".{mod}"
        # file inside a layer package
        if mod in ROOT_STAY or mod == "actions" or mod == "paths":
            return f"..{mod}"
        if target is None:
            return f"..{mod}"
        if target == current_layer:
            return f".{mod}"
        return f"..{target}.{mod}"

    def abs_import(mod: str) -> str:
        target = layer_of(mod)
        if target:
            return f"partner.{target}.{mod}"
        return f"partner.{mod}"

    # from .foo import / from .foo.bar (not ..)
    def repl_from_rel(match: re.Match[str]) -> str:
        dots, mod, rest = match.group(1), match.group(2), match.group(3)
        if dots != ".":
            return match.group(0)
        top = mod.split(".")[0]
        if top in {"core", "routing", "runtime", "office", "compose", "ops"}:
            return match.group(0)
        new = rel_import(top)
        if "." in mod:
            # from .workflow.x — shouldn't happen
            suffix = mod[len(top) :]
            new = new + suffix
        return f"from {new} import{rest}"

    text = re.sub(
        r"^from (\.+)([A-Za-z_][\w.]*) import(\s*\(?)",
        repl_from_rel,
        text,
        flags=re.M,
    )

    # from partner.foo import
    def repl_from_abs(match: re.Match[str]) -> str:
        mod, rest = match.group(1), match.group(2)
        top = mod.split(".")[0]
        if top in {"core", "routing", "runtime", "office", "compose", "ops"}:
            return match.group(0)
        return f"from {abs_import(top)}{mod[len(top):]} import{rest}"

    text = re.sub(
        r"^from partner\.([A-Za-z_][\w.]*) import(\s*\(?)",
        repl_from_abs,
        text,
        flags=re.M,
    )

    # import partner.foo
    def repl_import_abs(match: re.Match[str]) -> str:
        mod = match.group(1)
        top = mod.split(".")[0]
        if top in {"core", "routing", "runtime", "office", "compose", "ops"}:
            return match.group(0)
        return f"import {abs_import(top)}{mod[len(top):]}"

    text = re.sub(
        r"^import partner\.([A-Za-z_][\w.]*)",
        repl_import_abs,
        text,
        flags=re.M,
    )

    # patch("partner.foo...
    def repl_patch(match: re.Match[str]) -> str:
        quote, mod = match.group(1), match.group(2)
        top = mod.split(".")[0]
        if top in {"core", "routing", "runtime", "office", "compose", "ops", "actions", "paths"}:
            return match.group(0)
        return f"{quote}{abs_import(top)}{mod[len(top):]}"

    text = re.sub(
        r"""(['"])partner\.([A-Za-z_][\w.]*)""",
        repl_patch,
        text,
    )
    return text


def main() -> None:
    for layer in sorted(set(LAYERS.values())):
        d = PARTNER / layer
        d.mkdir(exist_ok=True)
        init = d / "__init__.py"
        if not init.exists():
            init.write_text(f'"""Partner {layer} layer."""\n', encoding="utf-8")

    # move modules
    for mod, layer in LAYERS.items():
        src = PARTNER / f"{mod}.py"
        dest = PARTNER / layer / f"{mod}.py"
        if src.exists():
            shutil.move(str(src), str(dest))
            print(f"move {mod}.py -> {layer}/")

    # rewrite all python under partner + tests
    for path in list(PARTNER.rglob("*.py")) + list(TESTS.glob("*.py")):
        rel = path.relative_to(PARTNER) if path.is_relative_to(PARTNER) else None
        current_layer = None
        if rel is not None and len(rel.parts) >= 2:
            maybe = rel.parts[0]
            if maybe in set(LAYERS.values()):
                current_layer = maybe
        elif path.parent == PARTNER:
            current_layer = None
        else:
            current_layer = None
        if path.parent.name == "tests" or path.is_relative_to(TESTS):
            current_layer = None  # tests use absolute partner.*
            original = path.read_text(encoding="utf-8")
            updated = rewrite_text(original, current_layer=None)
            # force abs rewrite only for tests — rewrite_text with None treats as root
            # but tests need partner.layer.mod — with current_layer None, from partner.X works via abs
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                print(f"rewrite {path}")
            continue
        original = path.read_text(encoding="utf-8")
        updated = rewrite_text(original, current_layer=current_layer)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"rewrite {path.relative_to(ROOT)}")

    # __main__ stays
    main_py = PARTNER / "__main__.py"
    main_py.write_text("from partner.ops.cli import main\n\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n", encoding="utf-8")
    print("updated __main__.py")


if __name__ == "__main__":
    main()
