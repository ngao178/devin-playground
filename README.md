# devin-playground

## Devin session on new GitHub issue

`.github/workflows/devin-on-issue.yml` starts a Devin session whenever an issue
is opened (or when the `devin` label is added to an existing issue) and comments
the session link back on the issue.

### Setup

1. Add your Devin API key (Devin settings → API keys) as a repository secret
   named `DEVIN_API_KEY`.
2. Optional: create a `devin` issue label so existing issues can be handed off
   after the fact.

### Behavior

- Trigger: `issues: [opened, labeled]`; the job runs when the action is `opened`
  or the added label is `devin`.
- Prompt: issue URL, title, and body, plus instructions to reference the issue
  from the PR description and commit messages.
- Sessions are created with `idempotent: true`, so a re-run for the same issue
  reuses the existing session instead of spawning a duplicate; the issue comment
  is only posted for newly created sessions.

### Restrict to labeled issues only

Remove the `github.event.action == 'opened'` clause from the job's `if:` so only
issues labeled `devin` trigger a session.
