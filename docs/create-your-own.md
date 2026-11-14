# Create your own agents repository

This guide is for teams that want their own copy of this repository: their
own org name, their own personas, and their own release tags. If you only
want to *use* the agents in an application repository, see
[use-in-your-repo.md](use-in-your-repo.md).

## 1. Create the repository

Pick one:

- **From the template** (if this repository is marked as a template): click
  **Use this template → Create a new repository**. You get a clean history.
- **Copy it manually:**

  ```bash
  gh repo create <your-org>/<your-agents-repo> --private --clone
  cd <your-agents-repo>
  git pull https://github.com/platformr/github-actions-agents.git main
  git push -u origin main
  ```

- **Fork it** if you want to pull future changes from upstream. Forks of a
  private repository stay private and are tied to the parent's access, so a
  copy is usually simpler for another organization.

## 2. Replace the owner and repository name

The org and repository name appear in the README, the docs, the example
workflows, and the action metadata. Replace them in one pass (macOS `sed`
shown; on Linux drop the `''` after `-i`):

```bash
grep -rl --exclude-dir=.git -e 'platformr/github-actions-agents' -e 'author: platformr' . \
  | xargs sed -i '' \
      -e 's#platformr/github-actions-agents#<your-org>/<your-agents-repo>#g' \
      -e 's#author: platformr#author: <your-org>#g'
```

Then regenerate the persona actions (their `author:` comes from
`scripts/action.template.yml`) and confirm nothing is left:

```bash
python3 scripts/generate_actions.py
```

```bash
grep -rn --exclude-dir=.git platformr .
```

Update any remaining mentions by hand, such as the organization name in
the sharing step of [use-in-your-repo.md](use-in-your-repo.md).

## 3. Let other repositories use the actions

A **public** repository's actions can be used by anyone; skip this step.

A **private or internal** repository's actions can't be used by other
repositories until you allow it. Workflows fail with "repository not found"
until you do:

1. Open the agents repository → **Settings → Actions → General**.
2. Under **Access**, choose **Accessible from repositories in the
   '&lt;your-org&gt;' organization**.
3. Save.

If your organization restricts which actions may run (**Org settings →
Actions → General → Policies**), allow these:

- `<your-org>/<your-agents-repo>/*` (or `<your-org>/*`)
- `anthropics/claude-code-action@*`
- `aws-actions/configure-aws-credentials@*`
- `actions/checkout@*` (and `actions/upload-artifact@*` /
  `actions/download-artifact@*` if you use the plan-analysis example)

## 4. Check it locally

There is no CI workflow in this repository. Run these before you push:

```bash
python3 scripts/generate_actions.py --check
```

```bash
bash -n .github/actions/_shared/scripts/*.sh && python3 -m py_compile .github/actions/_shared/scripts/*.py
```

If you have the linters installed:

```bash
cfn-lint infra/cloudformation/*.yml infra/cloudformation/*/*.yml
```

```bash
actionlint examples/workflows/*.yml
```

```bash
shellcheck .github/actions/_shared/scripts/*.sh
```

## 5. Release and versioning

Consumers reference a persona action as
`<your-org>/<your-agents-repo>/.github/actions/<persona>@<ref>`. All
personas share the repository's tags.

The examples use `@v1`, which only resolves once that tag exists. Until
then, use `@main` or a commit SHA.

Cut a release from `main`:

```bash
git tag -a v1.0.0 -m "v1.0.0" && git push origin v1.0.0
```

Move the major tag so `@v1` consumers pick up compatible changes:

```bash
git tag -fa v1 -m "v1" v1.0.0 && git push -f origin v1
```

Use a new major tag (`v2`) when you rename or remove an input, change an
output, or change the report schema in a way that breaks consumers.

## 6. Add or change personas

Each persona is one directory under `.github/actions/<name>/`. The
`action.yml` in it is generated, so edit the source files and regenerate.

1. Create `.github/actions/<name>/` with:
   - `persona.json`:

     ```json
     {
       "title": "IAM Policy Review",
       "description": "One sentence shown in the action metadata.",
       "icon": "shield",
       "aws_access": "none",
       "role_template": "",
       "inputs": []
     }
     ```

     `aws_access` is `required`, `optional`, or `none`. A `none` persona has
     no `target-role-arn` input and needs only the Bedrock role.
   - `prompt.md`: what the persona checks and how it rates severity. It can
     use `{{CONTEXT_DIR}}`, `{{AWS_PROFILE}}`, `{{TARGET_REGION}}`,
     `{{TARGET_ENVIRONMENT}}`, `{{REPO}}`, `{{PR_NUMBER}}`, `{{PERSONA}}`,
     `{{ORG_RULES_VERSION}}`, and `{{COST_THRESHOLD_USD}}`.
   - `schema.json`: the report contract. Copy an existing persona's schema
     and keep `persona`, `verdict`, `findings` (with `id`, `severity`,
     `category`, `resource`, `description`, `recommendation`, `evidence`,
     optional `file`), and `summary_markdown`. No single quotes anywhere in
     the file.
   - `aws-allowed-tools.txt` (only if it uses AWS): one read-only Claude Code
     rule per line, e.g. `Bash(aws ec2 describe-vpcs *)`.
2. If it uses AWS, add `infra/cloudformation/<name>/<name>-role.yml`. Start
   from an existing persona role template so it keeps the OIDC trust
   parameters and the deny-data-reads policy, and grant only the read APIs
   that match `aws-allowed-tools.txt`.
3. Generate the action and check it:

   ```bash
   python3 scripts/generate_actions.py && python3 scripts/generate_actions.py --check
   ```

4. Add the persona to the table in the README and, if useful, to an example
   workflow.

Shared behaviour lives in `.github/actions/_shared/` (prompt sections, tool
allow and deny lists, scripts) and in `scripts/action.template.yml` (the
step wiring for every action). A change there affects every persona.
