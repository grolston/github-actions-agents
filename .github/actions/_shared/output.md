## Output

Return your result ONLY as the structured output defined by the provided
JSON schema. Set `persona` to `{{PERSONA}}`.

- `verdict`: `pass` (nothing to report), `findings` (one or more findings),
  or `error` (the inputs were missing, unreadable, or so incomplete that a
  review would be a guess).
- `findings[].id`: short stable ID, e.g. `QP-001`.
- `findings[].severity`: `critical`, `important`, or `suggestion`, per your
  persona's severity rules.
- `findings[].file`: `path:line` of the changed line that introduces the
  issue (new-file line numbers, as in the diff). Each finding becomes a
  review conversation on that line that someone must resolve, so point at
  the most specific line in this PR's diff. If the issue is not on a changed
  line, give the file that relates to it; omit `file` only when no file
  applies.
- `findings[].resource`: the named thing the finding is about: an AWS
  resource, or for code a function, class, job, or step. Keep `category`
  and `resource` stable between runs for the same issue: together with the
  file they identify the conversation, so two findings that share all
  three become one.
- `summary_markdown`: at most ~15 lines of GitHub markdown for a human
  reviewer: what you checked, what matters, and what to do next. No
  headings above level 4, no HTML, no @mentions.
