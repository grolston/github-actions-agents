#!/usr/bin/env python3
"""Generate <persona>/action.yml for every persona from one template.

Each persona is its own GitHub Action (uses: platformr/github-actions-agents/<persona>@v1)
but the step wiring lives in scripts/action.template.yml so the actions never drift.

  python3 scripts/generate_actions.py           # write all action.yml files
  python3 scripts/generate_actions.py --check   # fail if any are out of date (CI)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "action.template.yml"

ROLE_DESCRIPTIONS = {
    "required": (
        "Read-only role for this persona ({role_template}), assumed via OIDC "
        "to inspect the target account. Pass it from a repository or "
        "environment secret."
    ),
    "optional": (
        "Optional read-only role ({role_template}). When empty the agent "
        "runs with Bedrock access only and no AWS CLI permissions."
    ),
}


def personas() -> list[Path]:
    return sorted(p.parent for p in ROOT.glob("*/persona.json"))


def render(persona_dir: Path) -> str:
    meta = json.loads((persona_dir / "persona.json").read_text())
    name = persona_dir.name
    access = meta["aws_access"]
    flags = {f"input:{i}" for i in meta.get("inputs", [])}
    if access in ("required", "optional"):
        flags.add("aws")
    elif access != "none":
        raise SystemExit(f"{name}: invalid aws_access {access!r}")

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
        "TARGET_ROLE_REQUIRED": "true" if access == "required" else "false",
        "TARGET_ROLE_DESCRIPTION": ROLE_DESCRIPTIONS.get(access, "").format(**meta),
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
