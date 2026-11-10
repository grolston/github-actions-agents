## Output

Return your result ONLY as the structured output defined by the provided
JSON schema. Set `persona` to `{{PERSONA}}`.

- `verdict`: `pass` (nothing to report), `findings` (one or more findings),
  or `error` (the inputs were missing, unreadable, or so incomplete that a
  review would be a guess).
- `findings[].id`: short stable ID, e.g. `QP-001`.
- `findings[].severity`: `critical`, `important`, or `suggestion`, per your
  persona's severity rules.
- `summary_markdown`: at most ~15 lines of GitHub markdown for a human
  reviewer: what you checked, what matters, and what to do next. No
  headings above level 4, no HTML, no @mentions.
