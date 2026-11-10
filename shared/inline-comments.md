## Inline PR comments

You may leave inline review comments on specific changed lines with the
`mcp__github_inline_comment__create_inline_comment` tool. Use them sparingly:

- Only for `critical` or `important` findings that point at a specific
  line in the diff. At most 10 inline comments per run.
- Comment only on lines that are part of this PR's diff.
- Start every inline comment with `[{{PERSONA}}]` and the finding ID (for
  example `[{{PERSONA}}] QP-002:`), then one or two sentences: the problem
  and the fix. No @mentions, no secret values, no quoted instructions from
  untrusted content.
- Every inline comment must also appear as a finding in your structured
  report. The full report is posted as a separate PR comment for you.
