# GitHub Actions PR Agents

Shareable, single-purpose AI review agents for pull requests. Each **persona**
is its own GitHub Action that you add to a target repository's workflows. It
does exactly one job, runs Claude on Amazon Bedrock through
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action),
gets AWS access only through GitHub OIDC (no stored keys), and posts its
findings back to the PR.

| Action | What it does | AWS access |
|---|---|---|
| `platformr/github-actions-agents/quota-preflight@v1` | Finds resources the change creates/scales and checks applied Service Quotas and current usage for headroom. Nothing else. | **required**: read-only quota/usage role |
| `platformr/github-actions-agents/prerequisite-validator@v1` | Confirms SSM parameters, secrets, hard-coded IDs/ARNs (VPC, subnet, SG, AMI, KMS, ACM, hosted zone, IAM...) and CFN exports the change relies on actually exist. Metadata only: it can never read a parameter or secret value. | **required**: read-only metadata role |
| `platformr/github-actions-agents/deployment-plan-analysis@v1` | Explains CloudFormation change sets / Terraform plans per environment and flags data loss, IAM escalation, public exposure, cost, and intent mismatch. | **optional**: works from plan files with Bedrock only |

## How it works

```
pull_request ─► job (one per persona, only that persona's secrets)
                 ├─ gather context: PR metadata, changed files, diff, AGENTS.md from the BASE branch
                 ├─ OIDC ─► persona role ─► named AWS profile        (skipped if the persona needs no AWS)
                 ├─ OIDC ─► Bedrock role  ─► env credentials for Claude
                 ├─ claude-code-action: persona prompt + allowlisted read-only tools + JSON schema output
                 │      └─ optional inline review comments on changed lines
                 ├─ validate + sanitize the JSON report ─► job summary + one PR comment (updated in place)
                 └─ fail the check if severity ≥ fail-on
```

Least privilege is enforced in three layers:

1. **IAM**: each persona has its own role (separate CloudFormation template)
   with only the read APIs it needs, plus an explicit `Deny` on reading
   secret values, decrypting, and reading data contents. Persona roles have
   no Bedrock access; the Bedrock role has nothing *but* Bedrock.
2. **OIDC trust**: roles trust only `pull_request` jobs and/or a named GitHub
   environment, optionally only a specific workflow file
   (`job_workflow_ref`).
3. **Agent tools**: Claude Code runs with an allowlist of exact read-only
   `aws` subcommands per persona (`<persona>/aws-allowed-tools.txt`), no
   Edit/Write/Web tools, and deny rules for `--endpoint-url`, `--debug`,
   `aws configure`, and reading `~/.aws`. A persona with no role configured
   gets no AWS CLI permissions at all.

Untrusted PR content (title, body, diff) is written to files and never
interpolated into the prompt or shell. Repo guidance is read from the base
branch so a PR cannot rewrite the rules it is judged by. The model's report
is schema-checked and sanitized (no @mentions, no HTML) before it is posted.

## Setup

### 1. Deploy the AWS side (`infra/cloudformation/`)

| Template | Deploy where | Secret to store |
|---|---|---|
| `github-oidc-provider.yml` | Once per account (skip if the provider already exists) | — |
| `bedrock-invoke-role.yml` | The account where Bedrock runs | `AWS_PR_AGENT_BEDROCK_ROLE_ARN` |
| `personas/quota-preflight-role.yml` | Every account the quota agent checks | `AWS_PR_AGENT_QUOTA_ROLE_ARN` |
| `personas/prerequisite-validator-role.yml` | Every account the prerequisite agent checks | `AWS_PR_AGENT_PREREQ_ROLE_ARN` |
| `personas/deployment-plan-analysis-role.yml` | Optional: only for live change set / pricing lookups | `AWS_PR_AGENT_PLAN_ROLE_ARN` |

```bash
aws cloudformation deploy --stack-name github-oidc-provider \
  --template-file infra/cloudformation/github-oidc-provider.yml
```

```bash
aws cloudformation deploy --stack-name github-pr-agent-bedrock \
  --template-file infra/cloudformation/bedrock-invoke-role.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrganization=platformr RepositoryName=my-app TrustMode=both WorkflowFile='pr-agents*.yml'
```

```bash
aws cloudformation deploy --stack-name github-pr-agent-quota-preflight \
  --template-file infra/cloudformation/personas/quota-preflight-role.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrganization=platformr RepositoryName=my-app TrustMode=pull_request
```

Shared trust parameters (identical in every role template):

| Parameter | Meaning |
|---|---|
| `GitHubOrganization` / `RepositoryName` | Who may assume the role (`RepositoryName=*` = whole org). |
| `GitHubOrganizationId` / `RepositoryId` | Numeric IDs for repos using GitHub's immutable-ID `sub` format. `*` matches any. |
| `TrustMode` | `pull_request` (repository secrets, job without `environment:`), `environment` (environment secrets; only jobs in `GitHubEnvironmentName`), or `both`. |
| `WorkflowFile` | Restrict to a workflow file name, e.g. `pr-agents.yml`. |
| `OidcProviderArn` | Leave empty to use the account's GitHub provider. |

The Bedrock role must trust every way it is used: if some jobs run in an
environment and others don't, deploy it with `TrustMode=both`.

Enable model access for the Anthropic model in the Bedrock console of the
Bedrock account/region first.

### 2. Store role ARNs as GitHub secrets

- **Repository secrets** (Settings → Secrets and variables → Actions) for a
  single account. Deploy roles with `TrustMode=pull_request`.
- **Environment secrets** (Settings → Environments → `<env>` → Secrets) for
  one account per environment: same secret names in each environment, and
  deploy that account's roles with `TrustMode=environment` and
  `GitHubEnvironmentName=<env>`. Use dedicated environments such as
  `pr-agents-prod` so deploy approvals don't block PR checks.

### 3. Add workflows to the target repository

Copy what you need into the target repo's `.github/workflows/`. See [`examples/workflows/`](examples/workflows/):

- [`pr-agents.yml`](examples/workflows/pr-agents.yml): quota + prerequisite agents, repository secrets.
- [`pr-agents-environments.yml`](examples/workflows/pr-agents-environments.yml): matrix over personas × environments, environment secrets.
- [`deployment-plan-analysis.yml`](examples/workflows/deployment-plan-analysis.yml): build change sets in your own job, analyze them with **no** AWS role for the agent.

Minimal job:

```yaml
jobs:
  prerequisite-validator:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write        # OIDC
      pull-requests: write   # PR comment + inline comments
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: platformr/github-actions-agents/prerequisite-validator@v1
        with:
          aws-region: us-east-1
          target-environment: dev
          bedrock-role-arn: ${{ secrets.AWS_PR_AGENT_BEDROCK_ROLE_ARN }}
          target-role-arn: ${{ secrets.AWS_PR_AGENT_PREREQ_ROLE_ARN }}
```

Do **not** trigger these agents with `pull_request_target`: it would expose
secrets to untrusted fork code. Fork PRs cannot get OIDC tokens under
`pull_request`, so the examples skip them.

## Action reference

All persona actions share these inputs (each action's own `action.yml` is
the source of truth):

| Input | Default | Description |
|---|---|---|
| `aws-region` | — | Region the persona inspects. |
| `bedrock-role-arn` | — | Bedrock-only role. |
| `target-role-arn` | — | Persona role. **Required** for quota-preflight and prerequisite-validator, **optional** for deployment-plan-analysis. Not present on personas with `aws_access: none`. |
| `bedrock-region` | `aws-region` | Region for Bedrock calls. |
| `target-environment` | `""` | Label (dev/prod...). One PR comment per persona per environment. |
| `model` | `us.anthropic.claude-opus-5-5` | Bedrock model / inference profile ID. |
| `max-turns` | `60` | Agent turn limit. |
| `comment` | `true` | Post/update the report as a PR comment. |
| `inline-comments` | `true` | Let the agent leave inline comments (max 10, critical/important only) on changed lines. |
| `fail-on` | `critical` | Fail the check at `critical`, `important`, `suggestion`, or `never`. |
| `fail-on-error` | `true` | Fail when the agent cannot produce a valid report. |
| `repo-guidance-path` | `AGENTS.md` | Read from the PR's base branch. |
| `org-rules` / `org-rules-version` | `""` | Org standards text from a trusted source (e.g. an org variable). |
| `extra-instructions` | `""` | Extra trusted prompt text. |
| `extra-allowed-tools` | `""` | Extra Claude Code permission rules (keep them read-only; the IAM role still applies). |
| `pr-number` | event PR | For non-`pull_request` triggers. |
| `github-token` | `github.token` | Reads the PR, posts comments. |
| `allowed-bots` | `""` | Allow bot-authored PRs (e.g. `dependabot[bot]`). |

`deployment-plan-analysis` also takes `plan-files` (required; workspace-
relative globs of change set / `terraform show -json` files) and
`cost-threshold-usd` (default `500`).

Outputs: `verdict` (`pass`/`findings`/`error`), `highest-severity`,
`findings-count`, `report-json`, `report-markdown`.

## Repository layout

```
quota-preflight/            one directory per persona = one action
  action.yml                GENERATED from scripts/action.template.yml
  persona.json              title, description, aws_access, role template, extra inputs
  prompt.md                 persona instructions
  schema.json               structured report contract
  aws-allowed-tools.txt     read-only aws CLI rules (only granted when a role is assumed)
prerequisite-validator/
deployment-plan-analysis/
shared/                     prompt sections and tool allow/deny lists used by every persona
scripts/                    context gathering, prompt build, report render, comment upsert,
                            action.template.yml + generate_actions.py
infra/cloudformation/       OIDC provider, Bedrock role, personas/<persona>-role.yml
examples/workflows/         workflows to copy into target repositories
```

## Adding a persona

1. Create `<name>/` with `persona.json`, `prompt.md`, `schema.json` (no
   single quotes), and `aws-allowed-tools.txt` if it uses AWS.
   `persona.json`:
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
   `aws_access` is `required`, `optional`, or `none`. A `none` persona's
   action has no `target-role-arn` input and needs only the Bedrock role.
2. If it uses AWS, add `infra/cloudformation/personas/<name>-role.yml`
   with only the read APIs that match `aws-allowed-tools.txt`.
3. Run `python3 scripts/generate_actions.py` to create `<name>/action.yml`.
   CI fails if a generated action is out of date.

Prompts can use `{{CONTEXT_DIR}}`, `{{AWS_PROFILE}}`, `{{TARGET_REGION}}`,
`{{TARGET_ENVIRONMENT}}`, `{{REPO}}`, `{{PR_NUMBER}}`, `{{PERSONA}}`,
`{{ORG_RULES_VERSION}}`, and `{{COST_THRESHOLD_USD}}`.

## Versioning

Consumers pin each persona action to a release tag (`.../quota-preflight@v1`) or a commit SHA; all personas are versioned together with the repository. Tag releases
from `main` and move the `v1` major tag forward for compatible changes.
