#!/usr/bin/env bash
# Collects everything the persona needs into a private context directory.
# Untrusted PR data (title, body, diff) is written to files and never
# interpolated into the prompt, shell commands, or workflow expressions.
set -euo pipefail

: "${PERSONA_DIR:?}" "${PERSONA:?}" "${REPO:?}" "${RUNNER_TEMP:?}"

if [[ ! "$PERSONA" =~ ^[a-z0-9][a-z0-9-]*$ ]] || [[ ! -f "$PERSONA_DIR/prompt.md" ]]; then
  echo "::error::Persona files for '$PERSONA' not found in $PERSONA_DIR."
  exit 1
fi

if [[ -n "${TARGET_ENVIRONMENT:-}" && ! "$TARGET_ENVIRONMENT" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "::error::target-environment may contain only letters, digits, '.', '_' and '-'."
  exit 1
fi

if [[ ! "${PR_NUMBER:-}" =~ ^[0-9]+$ ]]; then
  echo "::error::No pull request number. Run on a pull_request event or set the pr-number input."
  exit 1
fi

if ! git -C "${GITHUB_WORKSPACE:-.}" rev-parse --git-dir >/dev/null 2>&1; then
  echo "::error::The repository is not checked out. Add actions/checkout before this action."
  exit 1
fi

# Does this persona get AWS access? Driven by .github/actions/<persona>/persona.json.
aws_access=$(jq -r '.aws_access // "none"' "$PERSONA_DIR/persona.json")
use_aws=false
case "$aws_access" in
  required)
    if [[ -z "${TARGET_ROLE_ARN:-}" ]]; then
      echo "::error::Persona '$PERSONA' requires AWS access: set target-role-arn to its read-only role."
      exit 1
    fi
    use_aws=true ;;
  optional)
    [[ -n "${TARGET_ROLE_ARN:-}" ]] && use_aws=true ;;
  none)
    [[ -n "${TARGET_ROLE_ARN:-}" ]] && echo "::warning::Persona '$PERSONA' does not use AWS; ignoring target-role-arn."
    ;;
  *)
    echo "::error::$PERSONA/persona.json has invalid aws_access '$aws_access'."
    exit 1 ;;
esac
echo "AWS access for $PERSONA: $use_aws ($aws_access)"

suffix="$PERSONA${TARGET_ENVIRONMENT:+-$TARGET_ENVIRONMENT}"
ctx="$RUNNER_TEMP/pr-agent/$suffix"
rm -rf "$ctx"
mkdir -p "$ctx/plans"
profile="pr-agent-$suffix"

echo "Collecting PR #$PR_NUMBER metadata"
gh pr view "$PR_NUMBER" --repo "$REPO" \
  --json number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,labels,isDraft \
  > "$ctx/pull-request.json"

base_ref=$(jq -r .baseRefName "$ctx/pull-request.json")
base_sha=$(jq -r .baseRefOid "$ctx/pull-request.json")
head_sha=$(jq -r .headRefOid "$ctx/pull-request.json")

echo "Collecting changed files"
gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/files?per_page=100" \
  --jq '.[] | "\(.status)\t+\(.additions)/-\(.deletions)\t\(.filename)"' \
  > "$ctx/changed-files.tsv"

echo "Collecting diff"
if ! gh pr diff "$PR_NUMBER" --repo "$REPO" > "$ctx/diff.patch" 2>"$ctx/diff.err"; then
  # gh pr diff refuses very large diffs; fall back to local git if history is available.
  git -C "${GITHUB_WORKSPACE:-.}" fetch --no-tags --quiet origin "$base_sha" "$head_sha" 2>/dev/null || true
  if git -C "${GITHUB_WORKSPACE:-.}" diff "$base_sha...$head_sha" > "$ctx/diff.patch" 2>/dev/null; then
    echo "gh pr diff failed; used local git diff instead."
  else
    echo "DIFF UNAVAILABLE: $(cat "$ctx/diff.err")" > "$ctx/diff.patch"
    echo "::warning::Could not obtain the PR diff; the agent will work from the changed-file list."
  fi
fi
rm -f "$ctx/diff.err"
diff_bytes=$(wc -c < "$ctx/diff.patch" | tr -d ' ')

# Repo guidance comes from the BASE branch so the PR cannot change its own rules.
: > "$ctx/repo-guidance.md"
if [[ -n "${REPO_GUIDANCE_PATH:-}" ]]; then
  if gh api -H "Accept: application/vnd.github.raw" \
      "repos/$REPO/contents/${REPO_GUIDANCE_PATH}?ref=$base_ref" > "$ctx/repo-guidance.md" 2>/dev/null; then
    echo "Loaded $REPO_GUIDANCE_PATH from $base_ref"
  else
    : > "$ctx/repo-guidance.md"
    echo "No $REPO_GUIDANCE_PATH on $base_ref"
  fi
fi

printf '%s' "${ORG_RULES:-}" > "$ctx/org-rules.md"

plan_count=0
if [[ -n "${PLAN_FILES:-}" ]]; then
  shopt -s globstar nullglob
  cd "${GITHUB_WORKSPACE:-.}"
  while IFS= read -r pattern; do
    pattern="${pattern#"${pattern%%[![:space:]]*}"}"
    pattern="${pattern%"${pattern##*[![:space:]]}"}"
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue
    # shellcheck disable=SC2206
    matches=( $pattern )
    for f in "${matches[@]}"; do
      [[ -f "$f" ]] || continue
      if [[ "$f" == /* || "$f" == *..* ]]; then
        echo "::warning::Skipping plan file '$f': use paths relative to the workspace."
        continue
      fi
      mkdir -p "$ctx/plans/$(dirname "$f")"
      cp "$f" "$ctx/plans/$f"
      plan_count=$((plan_count + 1))
    done
  done <<< "$PLAN_FILES"
  if [[ $plan_count -eq 0 ]]; then
    echo "::warning::plan-files matched no files."
  fi
fi

{
  echo "dir=$ctx"
  echo "profile=$profile"
  echo "use-aws=$use_aws"
  echo "pr-number=$PR_NUMBER"
  echo "base-sha=$base_sha"
  echo "head-sha=$head_sha"
  echo "plan-count=$plan_count"
  echo "diff-bytes=$diff_bytes"
} >> "$GITHUB_OUTPUT"

echo "Context ready in $ctx ($(wc -l < "$ctx/changed-files.tsv" | tr -d ' ') files changed, $diff_bytes diff bytes, $plan_count plan files)"
