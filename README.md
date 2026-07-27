# devin-playground

## Devin session on labeled GitHub issue

`.github/workflows/devin-on-issue.yml` starts a Devin session whenever the
`devin` label is added to an issue.

### Setup

1. Create an issue label named `devin`.
2. Add your Devin API key (Devin settings → API keys) as a repository secret
   named `DEVIN_API_KEY`.

### Behavior

- Trigger: `issues: [labeled]`, gated on the added label being `devin`.
- Prompt: issue URL, title, and body, plus instructions to reference the issue
  from the PR description and commit messages, and to comment the PR URL on the
  issue once it is open.
- Sessions are created with `idempotent: true`, so re-labeling or re-running
  reuses the existing session instead of spawning a duplicate.
- The session URL is written to the workflow run summary.
