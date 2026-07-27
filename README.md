# devin-playground

A standalone webhook service that starts a Devin session when a GitHub issue is
labeled `devin`.

## How it works

1. GitHub sends an `issues` webhook to `POST /webhook`.
2. The request signature is verified against `GITHUB_WEBHOOK_SECRET`.
3. If the event is `labeled` with the trigger label (or `opened` with that label
   already applied), the app POSTs to
   `https://api.devin.ai/v3/organizations/{DEVIN_ORG_ID}/sessions` with a prompt
   built from the issue title, body, and URL.
4. The response returns the session id and URL; the session itself is instructed
   to open a PR referencing the issue and comment the PR URL back on it.

Every session is tagged `issue:<owner>/<repo>#<number>`, and the app looks that
tag up before creating anything, so replays or re-labeling reuse the existing
session instead of spawning duplicates.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in DEVIN_API_KEY, DEVIN_ORG_ID, GITHUB_WEBHOOK_SECRET
uvicorn app.main:app --reload --port 8000
```

Health check: `curl localhost:8000/healthz`

## Run with Docker

```bash
docker build -t devin-issue-webhook .
docker run --rm -p 8000:8000 --env-file .env devin-issue-webhook
```

## Point GitHub at it

The service is currently configured for
[`ngao178/superset`](https://github.com/ngao178/superset) via `ALLOWED_REPOS`;
issues from any other repo are ignored even if the signature is valid.

Expose the port publicly (deploy it, or `ngrok http 8000` while testing), then in
the repo: **Settings → Webhooks → Add webhook**

- Payload URL: `https://<your-host>/webhook`
- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET`
- Events: *Let me select individual events* → **Issues**

Create an issue label named `devin` (or set `TRIGGER_LABEL` to something else).

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DEVIN_API_KEY` | yes | — | Service user key (`cog_`) for the Devin v3 API |
| `DEVIN_ORG_ID` | yes | — | Org (`org-`) the sessions are created in |
| `GITHUB_WEBHOOK_SECRET` | yes | — | Verifies `X-Hub-Signature-256` |
| `DEVIN_API_URL` | no | `https://api.devin.ai` | API base URL |
| `TRIGGER_LABEL` | no | `devin` | Label that starts a session |
| `ALLOWED_REPOS` | no | — (all repos) | Comma-separated `owner/repo` allowlist |

## Tests

```bash
pytest
ruff check .
```
