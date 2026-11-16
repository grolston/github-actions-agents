#!/usr/bin/env python3
"""Compose the agent prompt and Claude Code CLI arguments.

Only trusted values (action inputs, repository metadata, file paths) are
substituted into the prompt. Untrusted PR content stays in files under
CONTEXT_DIR, which the agent reads as data.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path

# .github/actions/_shared/scripts/prepare.py -> .github/actions/_shared
SHARED = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def read_list(path: Path) -> list[str]:
    if not path.is_file():
        return []
    items = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line)
    return items


def render(template: str, values: dict[str, str]) -> str:
    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Unknown template variable {{{{{key}}}}}")
        return values[key]

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", sub, template)


def write_output(name: str, value: str) -> None:
    delim = f"EOF_{secrets.token_hex(16)}"
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"{name}<<{delim}\n{value}\n{delim}\n")


def fail(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(1)


def main() -> None:
    agent = env("AGENT")
    agent_dir = Path(os.environ["AGENT_DIR"])
    context_dir = env("CONTEXT_DIR")

    if not re.fullmatch(r"\d+", env("MAX_TURNS", "60")):
        fail("max-turns must be a positive integer")
    if not re.fullmatch(r"[A-Za-z0-9.:_/-]+", env("MODEL")):
        fail("model contains unexpected characters")
    if not re.fullmatch(r"\d+(\.\d+)?", env("COST_THRESHOLD_USD", "500")):
        fail("cost-threshold-usd must be a number")

    values = {
        "AGENT": agent,
        "REPO": env("REPO"),
        "PR_NUMBER": env("PR_NUMBER"),
        "CONTEXT_DIR": context_dir,
        "TARGET_REGION": env("TARGET_REGION"),
        "TARGET_ENVIRONMENT": env("TARGET_ENVIRONMENT") or "unspecified",
        "ORG_RULES_VERSION": env("ORG_RULES_VERSION", "unversioned"),
        "COST_THRESHOLD_USD": env("COST_THRESHOLD_USD", "500"),
    }

    use_aws = env("USE_AWS") == "true"

    parts = [
        render((SHARED / "preamble.md").read_text(), values),
        render((SHARED / ("aws-access.md" if use_aws else "no-aws-access.md")).read_text(), values),
        render((agent_dir / "prompt.md").read_text(), values),
    ]
    extra = env("EXTRA_INSTRUCTIONS")
    if extra:
        # Inserted verbatim (not templated): these come from the workflow author.
        parts.append("## Additional instructions from the workflow owner\n\n" + extra)
    parts.append(render((SHARED / "output.md").read_text(), values))

    prompt = "\n\n".join(parts)
    Path(context_dir, "prompt.md").write_text(prompt)

    schema = json.loads((agent_dir / "schema.json").read_text())
    schema_compact = json.dumps(schema, separators=(",", ":"))
    if "'" in schema_compact:
        fail(f"{agent}/schema.json must not contain single quotes")

    allowed = read_list(SHARED / "allowed-tools.txt")
    if use_aws:
        # AWS CLI rules are granted only to agents with aws_cli: true.
        allowed += read_list(SHARED / "aws-allowed-tools.txt") + read_list(agent_dir / "aws-allowed-tools.txt")
    allowed += read_list(agent_dir / "allowed-tools.txt")
    allowed += [t.strip() for t in env("EXTRA_ALLOWED_TOOLS").split(",") if t.strip()]
    disallowed = read_list(SHARED / "disallowed-tools.txt")
    for tool in allowed + disallowed:
        if any(ch in tool for ch in "\"'`$\n,"):
            fail(f"Tool rule contains a forbidden character: {tool!r}")
        if tool.split("(", 1)[0] in {"Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch"} and tool in allowed:
            fail(f"Agents are read-only; {tool!r} cannot be allowed")

    allowed_arg = ",".join(allowed)
    disallowed_arg = ",".join(disallowed)
    claude_args = "\n".join(
        [
            f"--model {env('MODEL')}",
            f"--max-turns {env('MAX_TURNS', '60')}",
            f'--add-dir "{context_dir}"',
            f'--allowedTools "{allowed_arg}"',
            f'--disallowedTools "{disallowed_arg}"',
            f"--json-schema '{schema_compact}'",
        ]
    )

    write_output("prompt", prompt)
    write_output("claude-args", claude_args)

    print(f"Agent: {agent} (AWS CLI: {use_aws})")
    print(f"Prompt: {len(prompt)} chars (saved to {context_dir}/prompt.md)")
    print("Allowed tools:\n  " + "\n  ".join(allowed))
    print("Denied tools:\n  " + "\n  ".join(disallowed))


if __name__ == "__main__":
    main()
