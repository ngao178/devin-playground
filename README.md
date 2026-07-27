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

## Dependency bump scanner

A second, independent path (no webhook involved) runs inside the same process.
Every `DEP_SCAN_INTERVAL_SECONDS` (default `150`, i.e. 2.5 minutes) it:

1. Reads the current `HEAD` of each repo in `DEP_SCAN_REPOS` (defaults to
   `ALLOWED_REPOS`). The first pass only records a baseline sha.
2. Diffs `last-scanned-sha...HEAD` via the GitHub compare API and looks for
   changed dependency manifests/lockfiles (`requirements*.txt`,
   `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `Gemfile`, …).
3. If any are touched, shallow-checks-out that commit into a temp dir and runs
   the ecosystem audit tools (`npm audit --json`, `pip-audit`), so the session
   gets a concrete vulnerability list (package, severity, installed range, fix
   version). Requires `git`, `npm`, and `pip-audit` on `PATH` (all installed in
   the Docker image); set `DEP_AUDIT_ENABLED=false` to skip it. Audit failures
   are logged and the scan continues without findings.
4. Then starts a Devin session tagged
   `depscan:<owner>/<repo>@<sha>` that checks the declared dependencies against
   the latest releases and **opens the bump pull requests** (one per ecosystem,
   `chore(deps): bump <ecosystem> dependencies`, versions ≥7 days old,
   lockfiles regenerated with the project's package manager).

The sha tag is the dedupe key, so a restart or an overlapping scan reuses the
existing session instead of opening duplicate PRs. Scanner sessions show up on
the dashboard with trigger `depscan`.

`POST /dep-scan` runs a scan immediately and returns what each repo scan found —
handy for testing without waiting for the interval.

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

The service is currently configured for
[`ngao178/superset`](https://github.com/ngao178/superset) via `ALLOWED_REPOS`;
issues from any other repo are ignored even if the signature is valid.

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
| `DEVIN_API_KEY` | yes | — | Service user key (`cog_`) for the Devin v3 API |
| `DEVIN_ORG_ID` | yes | — | Org (`org-`) the sessions are created in |
| `GITHUB_WEBHOOK_SECRET` | yes | — | Verifies `X-Hub-Signature-256` |
| `DEVIN_API_URL` | no | `https://api.devin.ai` | API base URL |
| `TRIGGER_LABEL` | no | `devin` | Label that starts a session |
| `ALLOWED_REPOS` | no | — (all repos) | Comma-separated `owner/repo` allowlist |
| `DEP_SCAN_ENABLED` | no | `true` | Turns the periodic dependency scanner on/off |
| `DEP_SCAN_INTERVAL_SECONDS` | no | `150` | Seconds between dependency scans |
| `DEP_SCAN_REPOS` | no | `ALLOWED_REPOS` | Comma-separated repos to scan |
| `DEP_AUDIT_ENABLED` | no | `true` | Run `npm audit --json` / `pip-audit` on the flagged commit |
| `DEP_AUDIT_TIMEOUT_SECONDS` | no | `300` | Per-command timeout for checkout and audits |
| `GITHUB_TOKEN` | no | — | Raises GitHub read rate limits; required for private repos |
| `LOG_LEVEL` | no | `INFO` | Root log level |

## Tests

```bash
pytest
ruff check .
```
