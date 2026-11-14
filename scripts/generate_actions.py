#!/usr/bin/env python3
"""Generate .github/actions/<persona>/action.yml for every persona from one template.

Each persona is its own GitHub Action (uses: platformr/github-actions-agents/.github/actions/<persona>@v1)
but the step wiring lives in scripts/action.template.yml so the actions never drift.

  python3 scripts/generate_actions.py           # write all action.yml files
  python3 scripts/generate_actions.py --check   # fail if any are out of date
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "action.template.yml"
ACTIONS = ROOT / ".github" / "actions"

def personas() -> list[Path]:
    return sorted(p.parent for p in ACTIONS.glob("*/persona.json"))


def render(persona_dir: Path) -> str:
    meta = json.loads((persona_dir / "persona.json").read_text())
    name = persona_dir.name
    if not isinstance(meta.get("aws_cli"), bool):
        raise SystemExit(f"{name}: persona.json needs \"aws_cli\": true or false")
    if not meta.get("role_template"):
        raise SystemExit(f"{name}: persona.json needs role_template (every persona has its own IAM role)")
    flags = {f"input:{i}" for i in meta.get("inputs", [])}

    out, stack = [], []
    for line in TEMPLATE.read_text().splitlines(keepends=True):
        m = re.match(r"#@(BEGIN|END) (\S+)", line)
        if m:
            if m.group(1) == "BEGIN":
                stack.append(m.group(2) in flags)
            else:
                stack.pop()
            continue
        if all(stack):
            out.append(line)
    if stack:
        raise SystemExit("unbalanced #@BEGIN/#@END in template")

    values = {
        "PERSONA": name,
        "TITLE": meta["title"],
        "DESCRIPTION": meta["description"],
        "ICON": meta.get("icon", "shield"),
        "ROLE_TEMPLATE": meta["role_template"],
    }
    text = "".join(out)
    text = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values[m.group(1)], text)
    return text


def main() -> None:
    check = "--check" in sys.argv
    stale = []
    for d in personas():
        target = d / "action.yml"
        text = render(d)
        if check:
            if not target.is_file() or target.read_text() != text:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.write_text(text)
            print(f"wrote {target.relative_to(ROOT)}")
    if stale:
        print("::error::Out of date (run python3 scripts/generate_actions.py): " + ", ".join(stale))
        sys.exit(1)


if __name__ == "__main__":
    main()
