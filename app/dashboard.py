import html
from collections import Counter
from datetime import datetime, timezone

from app.store import TrackedSession

_STATUS_COLORS = {
    "running": "#2563eb",
    "working": "#2563eb",
    "finished": "#16a34a",
    "completed": "#16a34a",
    "blocked": "#d97706",
    "stopped": "#6b7280",
    "expired": "#6b7280",
    "failed": "#dc2626",
    "unknown": "#6b7280",
}


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _badge(status: str) -> str:
    color = _STATUS_COLORS.get(status.lower(), "#6b7280")
    return (
        f'<span class="badge" style="background:{color}">{html.escape(status)}</span>'
    )


def _stats(sessions: list[TrackedSession]) -> dict[str, object]:
    issues = {
        (s.repository, s.issue_number) for s in sessions if s.issue_number is not None
    }
    active = [s for s in sessions if s.is_active]
    return {
        "issues_addressed": len(issues),
        "total_sessions": len(sessions),
        "active_sessions": len(active),
        "completed_sessions": len(sessions) - len(active),
        "by_status": Counter(s.status for s in sessions),
        "by_repository": Counter(s.repository for s in sessions),
        "by_source": Counter(s.source for s in sessions),
    }


def _stat_card(label: str, value: object) -> str:
    return (
        '<div class="card">'
        f'<div class="card-value">{value}</div>'
        f'<div class="card-label">{html.escape(label)}</div>'
        "</div>"
    )


def _breakdown(title: str, counts: Counter) -> str:
    if not counts:
        return ""
    rows = "".join(
        f'<div class="bd-row"><span>{html.escape(str(name))}</span>'
        f"<span>{count}</span></div>"
        for name, count in counts.most_common()
    )
    return f'<div class="breakdown"><h3>{html.escape(title)}</h3>{rows}</div>'


def _session_row(session: TrackedSession) -> str:
    return (
        "<tr>"
        f'<td><a href="{html.escape(session.url)}" target="_blank" '
        f'rel="noopener">{html.escape(session.session_id)}</a></td>'
        f"<td>{html.escape(session.subject)}</td>"
        f"<td>{html.escape(session.source)}</td>"
        f"<td>{_badge(session.status)}</td>"
        f"<td>{_fmt_time(session.created_at)}</td>"
        f"<td>{_fmt_time(session.updated_at)}</td>"
        "</tr>"
    )


def _scan_panel(scan_enabled: bool, scan_repos: tuple[str, ...]) -> str:
    if not scan_enabled:
        return (
            '<div class="scan"><span class="scan-hint">Dependency scanner is '
            "disabled (set DEP_SCAN_ENABLED and DEP_SCAN_REPOS).</span></div>"
        )
    repos = html.escape(", ".join(scan_repos))
    return (
        '<div class="scan">'
        '<button id="scan-btn" onclick="runScan()">Run dependency scan</button>'
        f'<span class="scan-hint">Audits {repos} and starts a bump session for '
        "any vulnerabilities found.</span>"
        '<pre id="scan-out" hidden></pre>'
        "</div>"
    )


_SCAN_SCRIPT = """
<script>
let scanning = false;
async function runScan() {
  const btn = document.getElementById('scan-btn');
  const out = document.getElementById('scan-out');
  scanning = true;
  btn.disabled = true;
  btn.textContent = 'Scanning\\u2026';
  out.hidden = false;
  out.textContent = 'Running the audit, this can take a few minutes\\u2026';
  try {
    const response = await fetch('/dep-scan', {method: 'POST'});
    const body = await response.json();
    const scans = body.scans || [];
    out.textContent = scans.length
      ? scans.map(function (scan) {
          const session = scan.session_url ? ' -> ' + scan.session_url : '';
          const findings = (scan.findings || []).length
            ? '\\n    ' + scan.findings.join('\\n    ')
            : '';
          return scan.repository + ': ' + scan.reason + session + findings;
        }).join('\\n')
      : JSON.stringify(body);
  } catch (err) {
    out.textContent = 'Scan failed: ' + err;
  } finally {
    scanning = false;
    btn.disabled = false;
    btn.textContent = 'Run dependency scan';
  }
}
setInterval(function () { if (!scanning) { location.reload(); } }, 15000);
</script>
"""


def render_dashboard(
    sessions: list[TrackedSession],
    scan_enabled: bool = False,
    scan_repos: tuple[str, ...] = (),
) -> str:
    stats = _stats(sessions)
    cards = "".join(
        [
            _stat_card("Issues addressed", stats["issues_addressed"]),
            _stat_card("Total sessions", stats["total_sessions"]),
            _stat_card("Active sessions", stats["active_sessions"]),
            _stat_card("Completed sessions", stats["completed_sessions"]),
        ]
    )
    breakdowns = (
        _breakdown("By status", stats["by_status"])
        + _breakdown("By repository", stats["by_repository"])
        + _breakdown("By trigger", stats["by_source"])
    )

    if sessions:
        rows = "".join(_session_row(s) for s in sessions)
        table = (
            "<table><thead><tr>"
            "<th>Session</th><th>Subject</th><th>Trigger</th><th>Status</th>"
            "<th>Created</th><th>Updated</th>"
            "</tr></thead><tbody>"
            f"{rows}</tbody></table>"
        )
    else:
        table = '<p class="empty">No sessions have been started yet.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Devin session dashboard</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0;
          background: #f6f7f9; color: #111827; }}
  header {{ background: #111827; color: #fff; padding: 20px 32px; }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header p {{ margin: 4px 0 0; color: #9ca3af; font-size: 13px; }}
  main {{ padding: 24px 32px; max-width: 1000px; margin: 0 auto; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
           padding: 18px; text-align: center; }}
  .card-value {{ font-size: 30px; font-weight: 700; }}
  .card-label {{ font-size: 13px; color: #6b7280; margin-top: 4px; }}
  .breakdowns {{ display: flex; gap: 16px; margin: 20px 0; flex-wrap: wrap; }}
  .breakdown {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
                padding: 12px 18px; min-width: 220px; }}
  .breakdown h3 {{ margin: 0 0 8px; font-size: 13px; color: #6b7280;
                   text-transform: uppercase; letter-spacing: .04em; }}
  .bd-row {{ display: flex; justify-content: space-between; padding: 3px 0;
             font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 14px; font-size: 14px;
            border-bottom: 1px solid #f0f1f3; }}
  th {{ background: #fafafa; color: #6b7280; font-weight: 600; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ color: #fff; padding: 2px 10px; border-radius: 999px;
            font-size: 12px; font-weight: 600; }}
  .empty {{ color: #6b7280; }}
  .scan {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
           margin-bottom: 20px; }}
  .scan button {{ background: #2563eb; color: #fff; border: 0; border-radius: 8px;
                  padding: 10px 16px; font-size: 14px; font-weight: 600;
                  cursor: pointer; }}
  .scan button:disabled {{ background: #93a7c9; cursor: progress; }}
  .scan-hint {{ color: #6b7280; font-size: 13px; }}
  .scan pre {{ flex-basis: 100%; background: #fff; border: 1px solid #e5e7eb;
               border-radius: 8px; padding: 12px; font-size: 13px;
               white-space: pre-wrap; margin: 0; }}
  a {{ color: #2563eb; text-decoration: none; }}
</style>
</head>
<body>
<header>
  <h1>Devin session dashboard</h1>
  <p>Sessions spun up from GitHub issues, tracked in memory since the last restart.</p>
</header>
<main>
  {_scan_panel(scan_enabled, scan_repos)}
  <div class="cards">{cards}</div>
  <div class="breakdowns">{breakdowns}</div>
  {table}
</main>
{_SCAN_SCRIPT if scan_enabled else ""}
</body>
</html>"""
