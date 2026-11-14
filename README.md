# GitHub Actions PR Agents

Shareable, single-purpose AI review agents for pull requests. Each **persona**
is its own GitHub Action that you add to a target repository's workflows. It
does exactly one job, runs Claude on Amazon Bedrock through
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action),
gets AWS access only through GitHub OIDC (no stored keys), and posts its
findings back to the PR.

| Action | What it does | AWS access |
|---|---|---|
| `platformr/github-actions-agents/.github/actions/quota-preflight@v1` | Finds resources the change creates/scales and checks applied Service Quotas and current usage for headroom. Nothing else. | **required**: read-only quota/usage role |
| `platformr/github-actions-agents/.github/actions/prerequisite-validator@v1` | Confirms SSM parameters, secrets, hard-coded IDs/ARNs (VPC, subnet, SG, AMI, KMS, ACM, hosted zone, IAM...) and CFN exports the change relies on actually exist. Metadata only: it can never read a parameter or secret value. | **required**: read-only metadata role |
| `platformr/github-actions-agents/.github/actions/deployment-plan-analysis@v1` | Explains CloudFormation change sets / Terraform plans per environment and flags data loss, IAM escalation, public exposure, cost, and intent mismatch. | **optional**: works from plan files with Bedrock only |

## How it works

```
pull_request ─► job (one per persona, only that persona's secrets)
                 ├─ gather context: PR metadata, changed files, diff, AGENTS.md from the BASE branch
                 ├─ OIDC ─► persona role ─► named AWS profile        (skipped if the persona needs no AWS)
                 ├─ OIDC ─► Bedrock role  ─► env credentials for Claude
                 ├─ claude-code-action: persona prompt + allowlisted read-only tools + JSON schema output
                 ├─ validate + sanitize the JSON report ─► job summary + one PR comment (updated in place)
                 ├─ one resolvable review thread per finding (deduplicated; auto-resolved when fixed)
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
   `aws` subcommands per persona (`.github/actions/<persona>/aws-allowed-tools.txt`), no
   Edit/Write/Web tools, and deny rules for `--endpoint-url`, `--debug`,
   `aws configure`, and reading `~/.aws`. A persona with no role configured
   gets no AWS CLI permissions at all.

Untrusted PR content (title, body, diff) is written to files and never
interpolated into the prompt or shell. Repo guidance is read from the base
branch so a PR cannot rewrite the rules it is judged by. The model's report
is schema-checked and sanitized (no @mentions, no HTML) before it is posted.

## Setup

1. **Share the actions** (private agents repository only): Settings →
   Actions → General → Access → *Accessible from repositories in the
   organization*.
2. **Deploy the AWS roles** from `infra/cloudformation/`: the OIDC provider
   (once per account), the Bedrock role, and the role for each persona that
   inspects AWS.
3. **Store the role ARNs** as repository or environment secrets in the app
   repository.
4. **Add a workflow** from [`examples/workflows/`](examples/workflows/) with
   `id-token: write` and `pull-requests: write`.

Step-by-step guides:

- [**Use the agents in your repository**](docs/use-in-your-repo.md):
  prerequisites, AWS stacks and trust modes, secrets, the workflow,
  requiring conversation resolution, an optional GitHub App identity, a
  first-run checklist, and troubleshooting.
- [**Create your own agents repository**](docs/create-your-own.md): copy
  this repository under your org, share it, release and tag it, and add
  personas.

Minimal job (use `@main` or a commit SHA until a `v1` tag exists):

```yaml
jobs:
  prerequisite-validator:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write        # OIDC
      pull-requests: write   # report comment + review threads
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: platformr/github-actions-agents/.github/actions/prerequisite-validator@v1
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
| `review-threads` | `true` | Open one resolvable review thread per finding at or above `thread-on` (see [Review threads](#review-threads)). |
| `thread-on` | `important` | Lowest severity that opens a thread: `critical`, `important`, or `suggestion`. |
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
`findings-count`, `report-json`, `report-markdown`, `open-threads`.

## Review threads

Every agent turns its findings into PR conversations that need resolution.
The action posts them itself from the validated report, so it does not
depend on the model choosing to comment:

- **One thread per finding** at or above `thread-on`, on the finding's
  changed line. If that line isn't in the diff, the thread goes on the
  file; if the file isn't in the PR (an account quota, a missing SSM
  parameter), it goes on the first changed file and names the real
  location.
- **No duplicates.** Each thread carries a hidden marker with the persona,
  environment, and a fingerprint of the finding (category + resource +
  file), so re-runs on new pushes reuse the existing thread.
- **Auto-resolve.** When a later run no longer reports a finding, the agent
  replies and resolves its thread. If it comes back, the agent reopens it.
  A thread a person resolved stays resolved (that is how a finding is
  accepted). A failed run (`verdict: error`) never resolves anything.
- At most 25 new threads per run per persona.

To make merges wait on these conversations, enable **Require conversation
resolution before merging** in the branch protection rule or ruleset for the
target branch. The action can't turn that on for you.

## Repository layout

```
.github/actions/
  quota-preflight/          one directory per persona = one action
    action.yml              GENERATED from scripts/action.template.yml
    persona.json            title, description, aws_access, role template, extra inputs
    prompt.md               persona instructions
    schema.json             structured report contract
    aws-allowed-tools.txt   read-only aws CLI rules (only granted when a role is assumed)
  prerequisite-validator/
  deployment-plan-analysis/
  _shared/                  prompt sections + tool allow/deny lists used by every persona
    scripts/                context gathering, prompt build, report render, comment upsert
infra/cloudformation/
  github-oidc-provider.yml  shared: once per account
  bedrock-invoke-role.yml   shared: Bedrock-only role used by every persona
  <persona>/<persona>-role.yml   read-only OIDC role for each persona that needs AWS
scripts/                    action.template.yml + generate_actions.py (repo tooling)
examples/workflows/         workflows to copy into target repositories
docs/                       setup guides
```

## Adding a persona

Create `.github/actions/<name>/` with `persona.json`, `prompt.md`,
`schema.json`, and (if it uses AWS) `aws-allowed-tools.txt` plus
`infra/cloudformation/<name>/<name>-role.yml`, then run
`python3 scripts/generate_actions.py`. Details:
[create-your-own.md → Add or change personas](docs/create-your-own.md#6-add-or-change-personas).

## Versioning

Consumers pin each persona action to a release tag
(`.../.github/actions/quota-preflight@v1`) or a commit SHA; all personas are
versioned together with the repository. No tag exists yet, so use `@main` or
a SHA for now. How to cut releases:
[create-your-own.md → Release and versioning](docs/create-your-own.md#5-release-and-versioning).
