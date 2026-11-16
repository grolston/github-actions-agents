#!/usr/bin/env python3
"""Validate the agent's structured output and render it as markdown.

The agent's output is model-authored and may echo untrusted PR content, so
it is validated against the agent contract, sanitized (no @mentions, no
HTML comments, no raw HTML), and size-limited before it is published.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

SEVERITY_ORDER = {"critical": 3, "important": 2, "suggestion": 1}
SEVERITY_ICON = {"critical": "🔴", "important": "🟠", "suggestion": "🔵"}
VERDICT_ICON = {"pass": "✅", "findings": "⚠️", "error": "❌"}
MAX_COMMENT_CHARS = 60000
MAX_CELL_CHARS = 600

# .github/actions/_shared/scripts -> .github/actions
ACTIONS = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _structured_from_messages(messages) -> dict | None:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("type") == "result":
            out = msg.get("structured_output")
            if isinstance(out, str):
                try:
                    out = json.loads(out)
                except json.JSONDecodeError:
                    return None
            if isinstance(out, dict):
                return out
    return None


def load_structured_output() -> tuple[dict | None, str]:
    path = env("EXECUTION_FILE")
    if path and Path(path).is_file():
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        messages = None
        try:
            data = json.loads(text)
            messages = data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            messages = []
            for line in text.splitlines():
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        found = _structured_from_messages(messages)
        if found is not None:
            return found, "execution_file"

    raw = env("STRUCTURED_OUTPUT")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data, "structured_output"
        except json.JSONDecodeError:
            return None, "structured_output was not valid JSON"
    outcome = env("AGENT_OUTCOME", "unknown")
    return None, f"agent produced no structured output (step outcome: {outcome})"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(report: dict, agent: str) -> list[str]:
    problems = []
    schema_path = Path(env("AGENT_DIR") or ACTIONS / agent) / "schema.json"
    required = []
    if schema_path.is_file():
        required = json.loads(schema_path.read_text()).get("required", [])
    for key in required:
        if key not in report:
            problems.append(f"missing required field '{key}'")
    if report.get("agent") not in (None, agent):
        problems.append(f"agent is '{report.get('agent')}', expected '{agent}'")
    if report.get("verdict") not in VERDICT_ICON:
        problems.append(f"invalid verdict '{report.get('verdict')}'")
    findings = report.get("findings", [])
    if not isinstance(findings, list):
        problems.append("findings is not a list")
    else:
        for i, f in enumerate(findings):
            if not isinstance(f, dict) or f.get("severity") not in SEVERITY_ORDER:
                problems.append(f"finding {i} has invalid severity")
    return problems


def redact_credentials(value):
    """Blank out this job's AWS credential values wherever the report repeats them.

    The agent role's credentials are in the agent's environment. The tool
    deny rules make them hard to read, but the report is the one thing the
    agent can publish, so the exact values are removed here as a backstop.
    """
    values = [v for v in (os.environ.get(k, "") for k in
                          ("AWS_SESSION_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID")) if len(v) >= 16]

    def walk(item):
        if isinstance(item, str):
            for v in values:
                item = item.replace(v, "[redacted credential]")
            return item
        if isinstance(item, list):
            return [walk(x) for x in item]
        if isinstance(item, dict):
            return {k: walk(x) for k, x in item.items()}
        return item

    return walk(value) if values else value


def error_report(agent: str, reason: str) -> dict:
    return {
        "agent": agent,
        "verdict": "error",
        "findings": [],
        "summary_markdown": f"The agent did not return a usable report: {reason}.",
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def sanitize(text) -> str:
    text = "" if text is None else str(text)
    text = text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    # Neutralize HTML tags and @mentions (no pings, no hidden markup).
    text = re.sub(r"<(/?[A-Za-z][^>]*)>", r"&lt;\1&gt;", text)
    text = re.sub(r"@(?=[A-Za-z0-9-])", "@​", text)
    return text


def cell(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"))
    text = sanitize(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 1] + "…"
    return text


def table(rows: list[dict], columns: list[str]) -> str:
    out = ["| " + " | ".join(c.replace("_", " ") for c in columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(row.get(c)) for c in columns) + " |")
    return "\n".join(out)


def columns_for(rows: list[dict]) -> list[str]:
    cols: list[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def title_case(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def render(report: dict, agent: str, marker: str, source_note: str) -> str:
    verdict = report.get("verdict", "error")
    findings = sorted(
        [f for f in report.get("findings", []) if isinstance(f, dict)],
        key=lambda f: -SEVERITY_ORDER.get(f.get("severity"), 0),
    )
    env_label = env("TARGET_ENVIRONMENT")
    region = env("TARGET_REGION")

    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITY_ORDER}
    scope = " · ".join(x for x in [env_label and f"`{env_label}`", region and f"`{region}`"] if x)

    lines = [
        marker,
        f"### {VERDICT_ICON.get(verdict, '❔')} {title_case(agent)} — {verdict}",
        "",
    ]
    if scope:
        lines.append(f"**Scope:** {scope}  ")
    lines.append(
        "**Findings:** "
        + ", ".join(f"{SEVERITY_ICON[s]} {counts[s]} {s}" for s in SEVERITY_ORDER)
    )
    lines.append("")

    summary = sanitize(report.get("summary_markdown", "")).strip()
    if summary:
        lines += [summary, ""]

    if findings:
        lines += ["#### Findings", ""]
        for f in findings:
            sev = f.get("severity", "")
            head = f"{SEVERITY_ICON.get(sev, '')} **{cell(sev).upper()}** · `{cell(f.get('category'))}`"
            if f.get("resource"):
                head += f" · `{cell(f.get('resource'))}`"
            if f.get("environment"):
                head += f" · {cell(f.get('environment'))}"
            lines.append(f"- {head}")
            for label, key in (("What", "description"), ("Fix", "recommendation"), ("Evidence", "evidence")):
                if f.get(key):
                    lines.append(f"  - _{label}:_ {cell(f.get(key))}")
        lines.append("")

    # Any other list-of-objects field (quotas, prerequisites, environments...)
    for key, value in report.items():
        if key == "findings" or not isinstance(value, list) or not value:
            continue
        rows = [v for v in value if isinstance(v, dict)]
        if not rows:
            continue
        lines += [
            f"<details><summary><b>{title_case(key)}</b> ({len(rows)})</summary>",
            "",
            table(rows, columns_for(rows)),
            "",
            "</details>",
            "",
        ]

    scalars = {k: v for k, v in report.items()
               if k not in {"agent", "verdict", "summary_markdown"} and isinstance(v, (str, int, float, bool))}
    if scalars:
        lines.append(" · ".join(f"**{cell(title_case(k))}:** {cell(v)}" for k, v in scalars.items()))
        lines.append("")

    run_url = env("RUN_URL")
    footer = f"<sub>Read-only review by the `{agent}` agent. Advisory only — it does not approve or deploy."
    if run_url:
        footer += f" [Run log]({run_url})."
    if source_note:
        footer += f" {sanitize(source_note)}"
    lines.append(footer + "</sub>")

    body = "\n".join(lines)
    if len(body) > MAX_COMMENT_CHARS:
        body = body[: MAX_COMMENT_CHARS - 200] + "\n\n_…report truncated; see the job summary artifact._\n" + footer + "</sub>"
    return body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print(f"{name}={value}")
        return
    with open(out, "a", encoding="utf-8") as fh:
        if "\n" in value:
            delim = f"EOF_{secrets.token_hex(8)}"
            fh.write(f"{name}<<{delim}\n{value}\n{delim}\n")
        else:
            fh.write(f"{name}={value}\n")


def main() -> None:
    agent = env("AGENT")
    context_dir = Path(env("CONTEXT_DIR") or ".")
    target_env = env("TARGET_ENVIRONMENT")

    report, source = load_structured_output()
    note = ""
    if report is None:
        report = error_report(agent, source)
    else:
        report = redact_credentials(report)
        problems = validate(report, agent)
        if problems:
            note = "Report failed validation: " + "; ".join(problems) + "."
            original = report
            report = error_report(agent, "; ".join(problems))
            report["raw_output"] = original

    findings = [f for f in report.get("findings", []) if isinstance(f, dict)]
    highest = max((SEVERITY_ORDER.get(f.get("severity"), 0) for f in findings), default=0)
    highest_name = next((k for k, v in SEVERITY_ORDER.items() if v == highest), "none")

    # Never let the model's own verdict hide findings.
    if report.get("verdict") == "pass" and findings:
        report["verdict"] = "findings"

    marker = f"<!-- pr-agent:{agent}{':' + target_env if target_env else ''} -->"
    markdown = render(report, agent, marker, note)

    json_path = context_dir / "report.json"
    md_path = context_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(markdown)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")

    fail_on = env("FAIL_ON", "critical").lower()
    threshold = SEVERITY_ORDER.get(fail_on, 99 if fail_on == "never" else 3)
    should_fail, reason = False, ""
    if report["verdict"] == "error" and env("FAIL_ON_ERROR", "true").lower() == "true":
        should_fail, reason = True, "Agent did not produce a valid report."
    elif highest >= threshold:
        should_fail, reason = True, f"Highest finding severity is '{highest_name}' (fail-on: {fail_on})."

    write_output("verdict", report["verdict"])
    write_output("highest-severity", highest_name)
    write_output("findings-count", str(len(findings)))
    write_output("report-json", str(json_path))
    write_output("report-markdown", str(md_path))
    write_output("marker", marker)
    write_output("should-fail", "true" if should_fail else "false")
    write_output("fail-reason", reason)
    print(f"verdict={report['verdict']} highest={highest_name} findings={len(findings)} source={source}")


if __name__ == "__main__":
    main()
