# devin-playground

A standalone webhook service that starts a Devin session when a GitHub issue is
labeled `devin`.

## How it works

1. GitHub sends an `issues` webhook to `POST /webhook`.
2. The request signature is verified against `GITHUB_WEBHOOK_SECRET`.
3. If the event is `labeled` with the trigger label (or `opened` with that label
   already applied), the app POSTs to `https://api.devin.ai/v1/sessions` with a
   prompt built from the issue title, body, and URL.
4. The response returns the session id and URL; the session itself is instructed
   to open a PR referencing the issue and comment the PR URL back on it.

Sessions are created with `idempotent: true`, so replays or re-labeling reuse the
existing session instead of spawning duplicates.

Every session that is spun up is recorded in an in-memory store, so you can list
the sessions and their current states (see below). The store lives in the process
memory and is reset on restart.

## List tracked sessions

```bash
# All sessions tracked since the process started
curl localhost:8000/sessions

# Only sessions that are still active (not finished/expired/blocked/...)
curl 'localhost:8000/sessions?active=true'

# Poll the Devin API for the latest status of each session before listing
curl 'localhost:8000/sessions?refresh=true'
```

Response shape:

```json
{
  "count": 1,
  "sessions": [
    {
      "session_id": "devin-123",
      "url": "https://app.devin.ai/sessions/123",
      "repository": "ngao178/devin-playground",
      "issue_number": 7,
      "status": "running",
      "created_at": 1737940000.0,
      "updated_at": 1737940000.0,
      "is_active": true
    }
  ]
}
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in DEVIN_API_KEY and GITHUB_WEBHOOK_SECRET
uvicorn app.main:app --reload --port 8000
```

Health check: `curl localhost:8000/healthz`

## Run with Docker

```bash
docker build -t devin-issue-webhook .
docker run --rm -p 8000:8000 --env-file .env devin-issue-webhook
```

## Point GitHub at it

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
| `DEVIN_API_KEY` | yes | — | Auth for the Devin API |
| `GITHUB_WEBHOOK_SECRET` | yes | — | Verifies `X-Hub-Signature-256` |
| `DEVIN_API_URL` | no | `https://api.devin.ai/v1` | API base URL |
| `TRIGGER_LABEL` | no | `devin` | Label that starts a session |

## Tests

```bash
pytest
ruff check .
```
