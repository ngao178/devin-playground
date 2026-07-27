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

Every session that is spun up is recorded in an in-memory store, so you can watch
the sessions and their current states on a dashboard (see below). The store lives
in the process memory and is reset on restart.

## Session dashboard

Open `http://localhost:8000/` (also served at `/sessions`) in a browser for an
HTML dashboard that shows:

- **Summary cards**: issues addressed, total sessions, active sessions, completed
  sessions.
- **Breakdowns** by status and by repository.
- **A table** of every tracked session with its status badge, linked session URL,
  originating issue, and created/updated timestamps.

The dashboard polls the Devin API for the latest status of each session on every
load and auto-reloads every 15 seconds, so the numbers update on their own. Pass
`?refresh=false` (e.g. `http://localhost:8000/?refresh=false`) to skip the live
status poll and render the last-known statuses instantly.

A session counts as *active* until its status is terminal (`finished`, `expired`,
`exit`, `stopped`, `cancelled`, `failed`). A blocked session waiting for user
input still counts as active since it isn't resolved yet.

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
cp .env.example .env   # fill in DEVIN_API_KEY and GITHUB_WEBHOOK_SECRET
docker build -t devin-issue-webhook .
docker run --rm -p 8000:8000 --env-file .env devin-issue-webhook
```

## Run with Docker Compose (webhook + ngrok)

`docker-compose.yml` runs the webhook and an ngrok tunnel that exposes it
publicly, so GitHub can reach it without a manual `ngrok` process.

```bash
cp .env.example .env   # fill in DEVIN_API_KEY, GITHUB_WEBHOOK_SECRET, NGROK_AUTHTOKEN
docker compose up --build
```

- Dashboard: http://localhost:8000/
- Public webhook URL: open the ngrok inspector at http://localhost:4040 and copy
  the `https://…ngrok…` forwarding URL; the webhook lives at `<that-url>/webhook`.

Get `NGROK_AUTHTOKEN` from https://dashboard.ngrok.com/get-started/your-authtoken.

Stop with `docker compose down`.

## Point GitHub at it

Expose the port publicly (the Compose `ngrok` service above, or `ngrok http 8000`
while testing), then in the repo: **Settings → Webhooks → Add webhook**

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
