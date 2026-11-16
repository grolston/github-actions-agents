#!/usr/bin/env python3
"""Open one resolvable PR review thread per finding and keep them in sync.

Runs after render_report.py, on the validated report only (never on raw
model output). For every finding at or above THREAD_ON severity:

- an open thread already exists for it   -> left alone (no duplicate)
- it was resolved by a person            -> left resolved (human decision)
- it was auto-resolved earlier, reappears -> reopened with a reply
  (a thread counts as auto-resolved unless GitHub names a different
  account as the resolver)
- no thread yet                          -> new review comment, anchored on
  the finding's changed line, else on its file, else on the first file the
  PR changes (so every finding is a conversation that needs resolution)

Open threads owned by this agent/environment whose finding is no longer
reported are replied to and resolved. Nothing is resolved when the report
is an error, so a failed run never clears real findings. If the token is
not allowed to resolve or reopen a thread, the run warns and carries on
rather than failing the check.

Threads are identified by a hidden marker holding a fingerprint of the
finding (category + resource + file path), not its ID, because IDs are
renumbered between runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_report import SEVERITY_ICON, SEVERITY_ORDER, cell, sanitize  # noqa: E402

MAX_NEW_THREADS = 25
MARKER_PREFIX = "<!-- pr-agent-thread:"
MARKER_RE = re.compile(r"<!-- pr-agent-thread:([A-Za-z0-9_.:-]+):([0-9a-f]{16}) -->")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def write_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print(f"{name}={value}")
        return
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

class GitHub:
    def __init__(self, token: str) -> None:
        self.token = token
        self.api = env("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.graphql_url = env("GITHUB_GRAPHQL_URL", self.api + "/graphql")

    def _send(self, method: str, url: str, body: dict | None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from None
        return json.loads(raw) if raw else {}

    def rest(self, method: str, path: str, body: dict | None = None) -> dict:
        return self._send(method, f"{self.api}/{path.lstrip('/')}", body)

    def graphql(self, query: str, variables: dict) -> dict:
        result = self._send("POST", self.graphql_url, {"query": query, "variables": variables})
        if result.get("errors"):
            raise RuntimeError(f"GraphQL error: {json.dumps(result['errors'])[:500]}")
        return result["data"]


THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          resolvedBy { login }
          comments(first: 1) {
            nodes { databaseId body author { __typename login } }
          }
        }
      }
    }
  }
}
"""

RESOLVE = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }"
UNRESOLVE = "mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { id } } }"


def existing_threads(gh: GitHub, repo: str, number: int, owner_key: str) -> dict[str, dict]:
    """Return {fingerprint: thread} for threads this agent/environment opened."""
    owner, name = repo.split("/", 1)
    threads: dict[str, dict] = {}
    after = None
    while True:
        data = gh.graphql(THREADS_QUERY, {"owner": owner, "name": name, "number": number, "after": after})
        page = data["repository"]["pullRequest"]["reviewThreads"]
        for node in page["nodes"]:
            first = (node["comments"]["nodes"] or [None])[0]
            if not first or (first.get("author") or {}).get("__typename") != "Bot":
                continue
            m = MARKER_RE.search(first.get("body") or "")
            if not m or m.group(1) != owner_key:
                continue
            threads[m.group(2)] = {
                "id": node["id"],
                "comment_id": first["databaseId"],
                "resolved": node["isResolved"],
                "resolved_by": (node.get("resolvedBy") or {}).get("login"),
                "author": first["author"]["login"],
            }
        if not page["pageInfo"]["hasNextPage"]:
            return threads
        after = page["pageInfo"]["endCursor"]


def _login(value) -> str:
    return str(value or "").removesuffix("[bot]").lower()


def auto_resolved(thread: dict) -> bool:
    """True when the thread was resolved by this agent rather than by a person.

    GitHub does not report a bot resolver consistently (login with or without
    the "[bot]" suffix, or no resolver at all), so a resolved thread is ours
    unless a different account is named as the resolver.
    """
    if not thread["resolved"]:
        return False
    resolver = _login(thread["resolved_by"])
    return not resolver or resolver == _login(thread["author"])


def one_line(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


# ---------------------------------------------------------------------------
# Diff anchoring
# ---------------------------------------------------------------------------

def parse_diff(patch: str) -> dict[str, set[int]]:
    """Map each file in the diff to the new-side line numbers a comment can target."""
    lines: dict[str, set[int]] = {}
    current: set[int] | None = None
    new_line = 0
    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            current = None
        elif raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = lines.setdefault(target[2:] if target.startswith("b/") else target, set())
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            new_line = int(m.group(1)) if m else 0
        elif current is not None and new_line:
            if raw.startswith("+") or raw.startswith(" "):
                current.add(new_line)
                new_line += 1
            elif raw.startswith("\\"):
                continue
            # "-" lines exist only on the old side.
    return lines


def changed_files(path: Path) -> list[str]:
    files = []
    if path.is_file():
        for row in path.read_text().splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and parts[0] != "removed":
                files.append(parts[2])
    return files


def parse_location(value) -> tuple[str, int | None]:
    text = str(value or "").strip().strip("`")
    if not text:
        return "", None
    m = re.match(r"^(.+?):(\d+)(?:-\d+)?$", text)
    path, line = (m.group(1), int(m.group(2))) if m else (text, None)
    path = path.removeprefix("./")
    if path.startswith("/") or ".." in Path(path).parts:
        return "", None
    return path, line


def fingerprint(finding: dict, path: str) -> str:
    def norm(v) -> str:
        return re.sub(r"\s+", " ", str(v or "")).strip().lower()

    key = "|".join([norm(finding.get("category")), norm(finding.get("resource")), path.lower()])
    if key == "||":
        key = norm(finding.get("id"))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Thread bodies
# ---------------------------------------------------------------------------

def thread_body(finding: dict, agent: str, marker: str, location_note: str) -> str:
    sev = finding.get("severity", "")
    head = f"{SEVERITY_ICON.get(sev, '')} **{cell(sev).upper()}** · `[{agent}] {cell(finding.get('id'))}` · `{cell(finding.get('category'))}`"
    if finding.get("resource"):
        head += f" · `{cell(finding.get('resource'))}`"
    if finding.get("environment"):
        head += f" · {cell(finding.get('environment'))}"
    lines = [marker, head, "", sanitize(finding.get("description")).strip(), ""]
    if finding.get("recommendation"):
        lines += [f"**Fix:** {sanitize(finding.get('recommendation')).strip()}", ""]
    if finding.get("evidence"):
        lines += [f"**Evidence:** {sanitize(finding.get('evidence')).strip()}", ""]
    if location_note:
        lines += [f"<sub>{location_note}</sub>", ""]
    lines.append(
        "<sub>Resolve this conversation once the finding is fixed or accepted. "
        "If a later run no longer reports it, the agent resolves it automatically.</sub>"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    agent = env("AGENT")
    target_env = env("TARGET_ENVIRONMENT")
    repo = env("REPO")
    number = int(env("PR_NUMBER"))
    head_sha = env("HEAD_SHA")
    context_dir = Path(env("CONTEXT_DIR"))
    report = json.loads(Path(env("REPORT_JSON")).read_text())
    threshold_name = env("THREAD_ON", "important").lower()
    if threshold_name not in SEVERITY_ORDER:
        print(f"::error::thread-on must be critical, important, or suggestion (got {threshold_name!r})")
        sys.exit(1)
    threshold = SEVERITY_ORDER[threshold_name]

    owner_key = agent + (f":{target_env}" if target_env else "")
    gh = GitHub(env("GH_TOKEN"))
    diff_lines = parse_diff((context_dir / "diff.patch").read_text(errors="replace"))
    files = changed_files(context_dir / "changed-files.tsv")
    anchor_file = next((f for f in files if f in diff_lines), files[0] if files else "")

    threads = existing_threads(gh, repo, number, owner_key)
    print(f"Found {len(threads)} existing {owner_key} thread(s)")

    if report.get("verdict") == "error":
        print("::warning::Report is an error; existing review threads were left untouched.")
        write_output("open-threads", str(sum(1 for t in threads.values() if not t["resolved"])))
        return

    findings = [f for f in report.get("findings", []) if isinstance(f, dict)]
    wanted: dict[str, tuple[dict, str, int | None]] = {}
    for f in sorted(findings, key=lambda f: -SEVERITY_ORDER.get(f.get("severity"), 0)):
        if SEVERITY_ORDER.get(f.get("severity"), 0) < threshold:
            continue
        path, line = parse_location(f.get("file"))
        wanted.setdefault(fingerprint(f, path), (f, path, line))

    opened = reopened = resolved = skipped = 0
    denied: list[str] = []  # resolve/reopen calls the token was not allowed to make
    for fp, (finding, path, line) in wanted.items():
        thread = threads.get(fp)
        if thread:
            if auto_resolved(thread):
                # We auto-resolved it earlier and it is back: reopen.
                try:
                    gh.graphql(UNRESOLVE, {"id": thread["id"]})
                except RuntimeError as exc:
                    denied.append(f"reopen: {one_line(exc)}")
                    continue
                gh.rest("POST", f"repos/{repo}/pulls/{number}/comments/{thread['comment_id']}/replies",
                        {"body": f"Detected again at {head_sha[:7]}; reopening."})
                reopened += 1
            continue
        if opened >= MAX_NEW_THREADS:
            skipped += 1
            continue

        marker = f"{MARKER_PREFIX}{owner_key}:{fp} -->"
        comment: dict = {"commit_id": head_sha}
        note = ""
        if path in diff_lines and line in diff_lines[path]:
            comment.update(path=path, line=line, side="RIGHT")
        elif path in files:
            comment.update(path=path, subject_type="file")
            if line:
                note = f"Applies to {cell(path)}:{line}, which is outside this PR's changed lines."
        elif anchor_file:
            comment.update(path=anchor_file, subject_type="file")
            note = (f"Applies to {cell(path)}{':' + str(line) if line else ''}, which this PR does not change."
                    if path else "Not tied to a specific file; anchored here so it can be tracked and resolved.")
        else:
            print(f"::warning::No file to anchor finding {finding.get('id')}; skipped.")
            skipped += 1
            continue
        comment["body"] = thread_body(finding, agent, marker, note)
        try:
            gh.rest("POST", f"repos/{repo}/pulls/{number}/comments", comment)
        except RuntimeError as exc:
            if comment.get("line") is None:
                raise
            # Line rejected (e.g. diff moved on): fall back to a file-level thread.
            print(f"::warning::Line comment failed ({exc}); retrying as a file comment.")
            comment.pop("line"), comment.pop("side")
            comment["subject_type"] = "file"
            gh.rest("POST", f"repos/{repo}/pulls/{number}/comments", comment)
        opened += 1

    stale_open = 0
    for fp, thread in threads.items():
        if fp in wanted or thread["resolved"]:
            continue
        # Resolve first and reply only on success, so a token that cannot
        # resolve threads does not add a reply on every run.
        try:
            gh.graphql(RESOLVE, {"id": thread["id"]})
        except RuntimeError as exc:
            denied.append(f"resolve: {one_line(exc)}")
            stale_open += 1
            continue
        gh.rest("POST", f"repos/{repo}/pulls/{number}/comments/{thread['comment_id']}/replies",
                {"body": f"No longer reported at {threshold_name} or above as of {head_sha[:7]}; resolved."})
        resolved += 1

    if skipped:
        print(f"::warning::{skipped} finding(s) did not get a review thread (limit {MAX_NEW_THREADS} new per run).")
    if denied:
        print(f"::warning::{len(denied)} review thread(s) could not be resolved or reopened with this token "
              f"and need a person to do it. First error: {denied[0]}")
    still_open = (sum(1 for fp, t in threads.items() if fp in wanted and not t["resolved"])
                  + opened + reopened + stale_open)
    print(f"threads: opened={opened} reopened={reopened} resolved={resolved} open={still_open}")
    write_output("open-threads", str(still_open))


if __name__ == "__main__":
    main()
