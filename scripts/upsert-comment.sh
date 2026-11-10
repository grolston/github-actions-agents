#!/usr/bin/env bash
# Creates or updates a single PR comment identified by a hidden marker, so
# re-runs replace the previous report instead of piling up comments.
set -euo pipefail

: "${REPO:?}" "${PR_NUMBER:?}" "${MARKER:?}" "${BODY_FILE:?}"

existing_id=$(gh api --paginate "repos/$REPO/issues/$PR_NUMBER/comments?per_page=100" \
  --jq ".[] | select(.user.type == \"Bot\") | select(.body | startswith(\"$MARKER\")) | .id" \
  | tail -n 1)

payload=$(jq -Rs '{body: .}' < "$BODY_FILE")

if [[ -n "$existing_id" ]]; then
  echo "$payload" | gh api -X PATCH "repos/$REPO/issues/comments/$existing_id" --input - >/dev/null
  echo "Updated comment $existing_id"
else
  echo "$payload" | gh api -X POST "repos/$REPO/issues/$PR_NUMBER/comments" --input - >/dev/null
  echo "Created report comment"
fi
