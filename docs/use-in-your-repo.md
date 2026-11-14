# Use the agents in your repository

This guide adds the PR review agents to an application repository: the
GitHub prerequisites, the AWS roles, the secrets, the workflow, and a
first-run check. To run your own copy of the agents repository instead, see
[create-your-own.md](create-your-own.md).

Throughout, `platformr/github-actions-agents` is the agents repository and
`<org>/<app-repo>` is the repository whose PRs you want reviewed.

## What you need

| Who | What |
|---|---|
| Admin of the agents repository | One-time: share its actions with the organization (step 1). |
| Someone with AWS access | Deploy the CloudFormation stacks (step 2). |
| Admin of the app repository | Add secrets and the workflow (steps 3–4); optionally require conversation resolution (step 5). |

No GitHub App, token, or org-owner action is needed: the agents comment
with the workflow's built-in token. An optional GitHub App gives each agent
its own name on the PR (step 6).

## 1. GitHub prerequisites

**Access to the actions (private agents repository only).** In
`platformr/github-actions-agents` → **Settings → Actions → General →
Access**, choose **Accessible from repositories in the 'platformr'
organization**. Without this, workflows fail with "repository not found".

**Allowed actions (only if your org restricts them).** In **Org settings →
Actions → General → Policies**, allow `platformr/github-actions-agents/*`,
`anthropics/claude-code-action@*`, `aws-actions/configure-aws-credentials@*`,
and `actions/checkout@*` (plus `actions/upload-artifact@*` and
`actions/download-artifact@*` for the plan-analysis example).

**Pick a ref.** The examples use `@v1`. Until a `v1` tag exists in the
agents repository, use `@main` or a commit SHA.

## 2. Deploy the AWS roles

All templates are in `infra/cloudformation/`.

**How the identities fit together:**

- **One OIDC provider per AWS account.** It lets GitHub jobs assume roles in
  that account. Every role in the account uses it.
- **One IAM role per persona.** It's the persona's only AWS identity: Claude
  Code uses it to call Bedrock, and the agent uses it for its read-only AWS
  CLI checks. Each job assumes it once.

Deploy only the roles for the personas you use:

| Template | Deploy where | Secret to store |
|---|---|---|
| `github-oidc-provider.yml` | Once per AWS account that has a persona role. Skip it if the account already has a `token.actions.githubusercontent.com` provider. | — |
| `quota-preflight/quota-preflight-role.yml` | Each account the quota agent checks | `AWS_PR_AGENT_QUOTA_ROLE_ARN` |
| `prerequisite-validator/prerequisite-validator-role.yml` | Each account the prerequisite agent checks | `AWS_PR_AGENT_PREREQ_ROLE_ARN` |
| `deployment-plan-analysis/deployment-plan-analysis-role.yml` | The account whose change sets it reads (or any account with Bedrock if it only reads plan files) | `AWS_PR_AGENT_PLAN_ROLE_ARN` |

Because each persona role calls Bedrock, Bedrock must be usable in every
account where you deploy one (see [Make sure the account can use the
model](#make-sure-the-account-can-use-the-model)), and model usage is billed
to that account.

### Decide how roles are trusted

Every role template takes the same trust parameters:

| Parameter | Meaning |
|---|---|
| `GitHubOrganization` / `RepositoryName` | Who may assume the role. `RepositoryName=*` trusts every repo in the org. |
| `GitHubOrganizationId` / `RepositoryId` | Numeric IDs, for repos that use GitHub's immutable-ID token format. `*` matches any. |
| `TrustMode` | `pull_request`: jobs without `environment:` (repository secrets). `environment`: only jobs in `GitHubEnvironmentName` (environment secrets). `both`: either. |
| `GitHubEnvironmentName` | The environment trusted when `TrustMode` is `environment` or `both`. |
| `WorkflowFile` | Only this workflow file may assume the role, e.g. `pr-agents.yml`. `*` allows any. |
| `OidcProviderArn` | Leave empty to use the account's GitHub provider. |

Two common setups:

- **One AWS account.** Deploy every role with `TrustMode=pull_request` and
  store the ARNs as repository secrets.
- **One account per environment (dev, prod...).** Create a GitHub
  environment per account, e.g. `pr-agents-dev` and `pr-agents-prod`. Deploy
  each account's persona roles with `TrustMode=environment` and
  `GitHubEnvironmentName=pr-agents-<env>`, and store the ARNs as environment
  secrets with the same names in each environment. Use dedicated
  environments so deploy approvals don't block PR checks.

To find the numeric IDs (only needed for the immutable-ID token format):

```bash
gh api orgs/<org> --jq .id
```

```bash
gh api repos/<org>/<app-repo> --jq .id
```

### Deploy

Run these from a checkout of the agents repository, with credentials for the
target account.

```bash
aws cloudformation deploy --stack-name github-oidc-provider \
  --template-file infra/cloudformation/github-oidc-provider.yml
```

```bash
aws cloudformation deploy --stack-name github-pr-agent-quota-preflight \
  --template-file infra/cloudformation/quota-preflight/quota-preflight-role.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrganization=<org> RepositoryName=<app-repo> TrustMode=pull_request
```

```bash
aws cloudformation deploy --stack-name github-pr-agent-prerequisite-validator \
  --template-file infra/cloudformation/prerequisite-validator/prerequisite-validator-role.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrganization=<org> RepositoryName=<app-repo> TrustMode=pull_request
```

Read each role ARN from its stack's `RoleArn` output:

```bash
aws cloudformation describe-stacks --stack-name github-pr-agent-quota-preflight --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text
```

### Make sure the account can use the model

In every account with a persona role, open the Bedrock console and confirm the Anthropic
model you'll use (default `us.anthropic.claude-opus-5-5`) is available. The
first Anthropic model in an account needs a one-time use-case form; if the
console still shows a **Model access** page, enable the model there. A `us.`
inference profile must be called from a US region (`bedrock-region`, or
`aws-region` when that's empty).

## 3. Store the role ARNs as secrets

In `<org>/<app-repo>`:

- **Repository secrets:** Settings → Secrets and variables → Actions → New
  repository secret.
- **Environment secrets:** Settings → Environments → `<env>` → Add
  environment secret. Use the same secret names in every environment.

Role ARNs aren't credentials, but keeping them in secrets keeps account IDs
out of logs and lets each environment supply its own.

## 4. Add the workflow

Copy one of the examples into `<org>/<app-repo>/.github/workflows/` and
adjust the region, environment names, and `paths:` filter:

- [`pr-agents.yml`](../examples/workflows/pr-agents.yml): quota and
  prerequisite agents with repository secrets.
- [`pr-agents-environments.yml`](../examples/workflows/pr-agents-environments.yml):
  each agent once per environment, with environment secrets.
- [`deployment-plan-analysis.yml`](../examples/workflows/deployment-plan-analysis.yml):
  your own job builds the change sets; the agent analyzes them with its
  persona role, which can't create or run change sets.

A minimal job:

```yaml
jobs:
  prerequisite-validator:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write        # OIDC to AWS
      pull-requests: write   # report comment + review threads
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: platformr/github-actions-agents/.github/actions/prerequisite-validator@main
        with:
          aws-region: us-east-1
          target-environment: dev
          role-arn: ${{ secrets.AWS_PR_AGENT_PREREQ_ROLE_ARN }}
```

Keep these as they are:

- **Trigger on `pull_request`, never `pull_request_target`.**
  `pull_request_target` runs with secrets on untrusted fork code.
- **The fork guard** (`head.repo.full_name == github.repository`). Fork PRs
  can't get OIDC tokens or secrets, so the job would only fail.
- **`pull-requests: write` on the job.** It overrides a read-only org
  default for this job only.

Optional: add an `AGENTS.md` to the app repository's default branch with
anything the agents should know (naming conventions, accounts, expected
quotas). The agents read it from the PR's base branch, so a PR can't change
its own rules.

## 5. Require conversation resolution (optional)

Each finding at or above `thread-on` (default `important`) opens a review
thread on the PR. To make merges wait until every thread is resolved:

1. In `<org>/<app-repo>` → **Settings → Rules → Rulesets** (or **Branches →
   Branch protection rules**), edit the rule for the default branch.
2. Turn on **Require conversation resolution before merging**.

Resolving a thread is how a reviewer accepts a finding. The agent also
resolves its own threads when a later run no longer reports the finding.
Set `thread-on: suggestion` to open threads for every finding, or
`review-threads: false` to post only the summary comment.

## 6. Give each agent its own name (optional)

By default every agent posts as `github-actions[bot]`, and the persona is
named in each comment. To post under an app's name instead, mint a GitHub
App token and pass it as `github-token`.

1. **Create the app** (org owner): Org settings → Developer settings →
   GitHub Apps → New GitHub App.
   - Name: e.g. `pr-agents` (comments show as `pr-agents[bot]`). Create one
     app per persona if you want a name per persona.
   - Homepage URL: any URL, e.g. the agents repository.
   - Webhook: untick **Active**.
   - Repository permissions: **Pull requests: Read and write**, **Contents:
     Read-only**.
   - Where can it be installed: **Only on this account**.
2. **Generate a private key** on the app page and note the **Client ID**.
3. **Install the app** on the app repositories.
4. **Store** the client ID as the org variable `PR_AGENT_APP_CLIENT_ID` and
   the key file's contents as the org secret `PR_AGENT_APP_PRIVATE_KEY`.
5. **Use it in the job:**

   ```yaml
       steps:
         - uses: actions/create-github-app-token@v3
           id: app-token
           with:
             client-id: ${{ vars.PR_AGENT_APP_CLIENT_ID }}
             private-key: ${{ secrets.PR_AGENT_APP_PRIVATE_KEY }}
         - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
           with: { persist-credentials: false }
         - uses: platformr/github-actions-agents/.github/actions/prerequisite-validator@main
           with:
             github-token: ${{ steps.app-token.outputs.token }}
             # ...same inputs as before
   ```

   Pin `actions/create-github-app-token` to a commit SHA, as the other
   actions are, and add it to your org's allowed actions if they're
   restricted.

The app token is used for reading the PR and posting comments. The agent
itself never gets tools that write to GitHub.

## 7. First run checklist

Open a test PR that changes an infrastructure file (it has to match the
workflow's `paths:` filter), then check:

- [ ] Each agent's job starts and passes **Assume persona role**.
- [ ] The PR gets one summary comment per agent (per environment).
- [ ] Findings at or above `thread-on` appear as review threads in **Files
      changed**.
- [ ] Pushing another commit updates the summary comment and opens no
      duplicate threads.
- [ ] Fixing a finding and pushing again resolves its thread with a "No
      longer reported" reply.
- [ ] The job's log shows the AWS calls the agent made, and none were
      denied by the tool allowlist that should have been allowed.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Unable to resolve action ... repository not found` | The agents repository is private and not shared with the org (step 1), or the ref (e.g. `@v1`) doesn't exist yet. |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The role's trust doesn't match the job: wrong `RepositoryName`, `TrustMode` doesn't match whether the job has `environment:`, the environment name differs, `WorkflowFile` doesn't match the workflow file name, or `id-token: write` is missing. |
| `Credentials could not be loaded` | The secret is empty in this context: an environment secret read by a job without `environment:`, or the reverse. |
| Bedrock `AccessDeniedException` | Model access not enabled in that account and region, or the `model` input doesn't match `BedrockModelIdPattern` on the persona role. |
| `Resource not accessible by integration` when commenting | The job is missing `pull-requests: write`, or the PR is from a fork or Dependabot (their token is read-only). |
| Verdict `error`, "agent produced no structured output" | The agent ran out of turns or hit an error; see the agent step's log. Raise `max-turns` if it was still working. |
| Threads are created but never auto-resolve, and **Sync review threads** warns that threads "could not be resolved or reopened" | The token isn't allowed to resolve review threads; the warning quotes GitHub's error. Resolve them by hand, or pass a `github-token` that is allowed to. |
