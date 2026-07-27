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

The app tracks a session per `(repository, issue)`, so replays or re-labeling of
the same issue reuse the existing tracked session instead of spawning duplicates.

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

A session counts as *active* until its status is terminal (`exit`, `error`,
`finished`, `expired`, `stopped`, `cancelled`, `failed`). A blocked session
waiting for user input still counts as active since it isn't resolved yet.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in DEVIN_API_KEY, DEVIN_ORG_ID and GITHUB_WEBHOOK_SECRET
uvicorn app.main:app --reload --port 8000
```

Health check: `curl localhost:8000/healthz`

## Run with Docker

```bash
cp .env.example .env   # fill in DEVIN_API_KEY, DEVIN_ORG_ID and GITHUB_WEBHOOK_SECRET
docker build -t devin-issue-webhook .
docker run --rm -p 8000:8000 --env-file .env devin-issue-webhook
```

## Run with Docker Compose (webhook + ngrok)

`docker-compose.yml` runs the webhook and an ngrok tunnel that exposes it
publicly, so GitHub can reach it without a manual `ngrok` process.

```bash
cp .env.example .env   # fill in DEVIN_API_KEY, DEVIN_ORG_ID, GITHUB_WEBHOOK_SECRET, NGROK_AUTHTOKEN
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
| `DEVIN_API_KEY` | yes | — | Org-scoped key (`ManageOrgSessions`) for the Devin v3 API |
| `DEVIN_ORG_ID` | yes | — | Devin org id (prefix `org-`) sessions are created under |
| `GITHUB_WEBHOOK_SECRET` | yes | — | Verifies `X-Hub-Signature-256` |
| `DEVIN_API_URL` | no | `https://api.devin.ai/v3` | API base URL |
| `TRIGGER_LABEL` | no | `devin` | Label that starts a session |

## Tests

```bash
pytest
ruff check .
```
