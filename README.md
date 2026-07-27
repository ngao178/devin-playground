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
