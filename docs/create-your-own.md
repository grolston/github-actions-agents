# Create your own agents repository

This guide is for teams that want their own copy of this repository: their
own org name, their own agents, and their own release tags. If you only
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
  git pull https://github.com/grolston/github-actions-agents.git main
  git push -u origin main
  ```

- **Fork it** if you want to pull future changes from upstream. A fork of
  a public repository is public; use a copy if yours must be private.

## 2. Replace the owner and repository name

The org and repository name appear in the README, the docs, the example
workflows, and the action metadata. Replace them in one pass (macOS `sed`
shown; on Linux drop the `''` after `-i`):

```bash
grep -rl --exclude-dir=.git -e 'grolston/github-actions-agents' -e 'author: grolston' . \
  | xargs sed -i '' \
      -e 's#grolston/github-actions-agents#<your-org>/<your-agents-repo>#g' \
      -e 's#author: grolston#author: <your-org>#g'
```

Then regenerate the agent actions (their `author:` comes from
`scripts/action.template.yml`) and confirm nothing is left:

```bash
python3 scripts/generate_actions.py
```

```bash
grep -rn --exclude-dir=.git grolston .
```

Update any remaining mentions by hand. If your copy is private, also
change the access paragraph in step 1 of
[use-in-your-repo.md](use-in-your-repo.md), which says this repository is
public.

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

Consumers reference an agent action as
`<your-org>/<your-agents-repo>/.github/actions/<agent>@<ref>`. All
agents share the repository's tags.

The examples use `@v1`, which only resolves once that tag exists. Until
then, use `@main` or a commit SHA.

Cut a release from `main`:

```bash
git tag -a v1.0.0 -m "v1.0.0" && git push origin v1.0.0
```

Move the major tag so `@v1` consumers pick up compatible changes:

```bash
git tag -fa v1 -m "v1" "v1.0.0^{}" && git push -f origin v1
```

Use a new major tag (`v2`) when you rename or remove an input, change an
output, or change the report schema in a way that breaks consumers.

## 6. Add or change agents

Each agent is one directory under `.github/actions/<name>/`. The
`action.yml` in it is generated, so edit the source files and regenerate.

1. Create `.github/actions/<name>/` with:
   - `agent.json`:

     ```json
     {
       "title": "IAM Policy Review",
       "description": "One sentence shown in the action metadata.",
       "icon": "shield",
       "aws_cli": false,
       "role_template": "infra/cloudformation/iam-policy-review/iam-policy-review-role.yml",
       "inputs": []
     }
     ```

     `aws_cli` decides whether the agent gets AWS CLI tools. Every agent
     has its own IAM role either way, because Claude runs on Bedrock with
     it.
   - `prompt.md`: what the agent checks and how it rates severity. It can
     use `{{CONTEXT_DIR}}`, `{{TARGET_REGION}}`, `{{TARGET_ENVIRONMENT}}`,
     `{{REPO}}`, `{{PR_NUMBER}}`, `{{AGENT}}`, `{{ORG_RULES_VERSION}}`,
     and `{{COST_THRESHOLD_USD}}`.
   - `schema.json`: the report contract. Copy an existing agent's schema
     and keep `agent`, `verdict`, `findings` (with `id`, `severity`,
     `category`, `resource`, `description`, `recommendation`, `evidence`,
     optional `file`), and `summary_markdown`. No single quotes anywhere in
     the file.
   - `aws-allowed-tools.txt` (only with `"aws_cli": true`): one read-only
     Claude Code rule per line, e.g. `Bash(aws ec2 describe-vpcs *)`.
2. Add its role at `infra/cloudformation/<name>/<name>-role.yml`. Copy an
   existing agent role template so it keeps the OIDC trust parameters, the
   `bedrock-invoke-anthropic` policy, and the `deny-data-reads` policy. Then
   replace the agent's read policy with only the read APIs that match
   `aws-allowed-tools.txt`, or remove it for an agent with
   `"aws_cli": false`.
3. Generate the action and check it:

   ```bash
   python3 scripts/generate_actions.py && python3 scripts/generate_actions.py --check
   ```

4. Add the agent to the table in the README and, if useful, to an example
   workflow.

Shared behaviour lives in `.github/actions/_shared/` (prompt sections, tool
allow and deny lists, scripts) and in `scripts/action.template.yml` (the
step wiring for every action). A change there affects every agent.
