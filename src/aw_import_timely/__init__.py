"""Import Timely time entries into ActivityWatch.

Timely is a time tracking tool with OAuth 2.0 authentication.
This tool fetches your time entries via the Timely REST API and imports
them into ActivityWatch so you can combine Timely history with AW data.

API docs: https://developer.timely.com/

## Authentication

Timely uses OAuth 2.0 (Authorization Code flow). You need to:

1. Create an OAuth application in Timely:
   Settings → Devs → Add application
   Use `http://localhost:8321/callback` as the redirect URI.

2. Run the auth command to get an access token:
   `aw-import-timely auth --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET`

3. Use the token for import:
   `TIMELY_ACCESS_TOKEN=... aw-import-timely preview`

Alternatively, set `TIMELY_ACCESS_TOKEN` directly if you already have one.
"""

from datetime import datetime, timezone
from typing import Optional

import requests
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Import Timely time entries into ActivityWatch")
console = Console()

TIMELY_API = "https://api.timelyapp.com/1.1"
BUCKET_ID = "aw-import-timely"


class TimelyClient:
    """Minimal Timely API client using OAuth 2.0 Bearer token."""

    def __init__(self, access_token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {access_token}"}
        )

    def _get(self, path: str, params: Optional[dict] = None) -> object:
        resp = self.session.get(f"{TIMELY_API}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    def get_identity(self) -> dict:
        """Get current user identity, including default account_id."""
        return self._get("/identity")  # type: ignore[return-value]

    def get_account(self, account_id: int) -> dict:
        """Get account details."""
        return self._get(f"/{account_id}/users/current")  # type: ignore[return-value]

    def get_hours(
        self, account_id: int, params: Optional[dict] = None
    ) -> list[dict]:
        """Get time entries (hours) for an account.

        API: GET /1.1/{account_id}/hours
        Supports filters: from, to, project, user, etc.
        """
        return self._get(f"/{account_id}/hours", params=params)  # type: ignore[return-value]

    def get_projects(self, account_id: int) -> dict[int, str]:
        """Return {projectId: projectName} mapping."""
        result: dict[int, str] = {}
        page = 1
        while True:
            batch = self._get(
                f"/{account_id}/projects",
                {"page": page, "per_page": 100},
            )
            batch_list = batch if isinstance(batch, list) else []
            for p in batch_list:
                pid = p.get("id")
                if pid is not None:
                    result[int(pid)] = p.get("name", str(pid))
            if len(batch_list) < 100:
                break
            page += 1
        return result

    def get_clients(self, account_id: int) -> dict[int, str]:
        """Return {clientId: clientName} mapping."""
        result: dict[int, str] = {}
        page = 1
        while True:
            batch = self._get(
                f"/{account_id}/clients",
                {"page": page, "per_page": 100},
            )
            batch_list = batch if isinstance(batch, list) else []
            for c in batch_list:
                cid = c.get("id")
                if cid is not None:
                    result[int(cid)] = c.get("name", str(cid))
            if len(batch_list) < 100:
                break
            page += 1
        return result


def _parse_timely_dt(s: str) -> datetime:
    """Parse Timely ISO 8601 datetime string to UTC datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _format_duration(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def build_events(
    entries: list[dict],
    projects: dict[int, str],
    clients: dict[int, str],
) -> list[dict]:
    """Convert Timely hours entries to AW event dicts.

    Timely hour entries look like:
    {
      "id": 123,
      "project": 456,
      "user": 789,
      "hours": "2025-03-15",
      "minutes": 480,        # total minutes
      "note": "Worked on X",
      "billable": True,
      ...
    }
    """
    events = []
    for entry in entries:
        # Timely "hours" entries have a date and minutes
        date_str = entry.get("hours")
        minutes = entry.get("minutes", 0)
        if not date_str or not minutes or minutes <= 0:
            continue

        # Use the date as the event timestamp (start of day)
        try:
            start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        duration = float(minutes) * 60.0  # convert to seconds

        project_id_raw = entry.get("project")
        project_id = int(project_id_raw) if project_id_raw is not None else 0

        client_id_raw = entry.get("client")
        client_id = int(client_id_raw) if client_id_raw is not None else 0

        events.append(
            {
                "timestamp": start.isoformat(),
                "duration": duration,
                "data": {
                    "title": entry.get("note") or "",
                    "project": projects.get(project_id, "No project"),
                    "client": clients.get(client_id, "") if client_id else "",
                    "billable": bool(entry.get("billable", False)),
                    "source": "timely",
                },
            }
        )
    return events


def _push_to_aw(events: list[dict], aw_host: str) -> None:
    """Create AW bucket and insert events."""
    base = aw_host.rstrip("/")
    # Create bucket (idempotent)
    requests.post(
        f"{base}/api/0/buckets/{BUCKET_ID}",
        json={
            "id": BUCKET_ID,
            "type": "app.active",
            "client": "aw-import-timely",
            "hostname": "import",
        },
        timeout=10,
    ).raise_for_status()

    # Insert in batches of 1000
    batch_size = 1000
    for i in range(0, len(events), batch_size):
        requests.post(
            f"{base}/api/0/buckets/{BUCKET_ID}/events",
            json=events[i : i + batch_size],
            timeout=30,
        ).raise_for_status()


def _resolve_account(
    access_token: str, account_id: Optional[int]
) -> tuple["TimelyClient", int]:
    """Initialize client and resolve account ID."""
    client = TimelyClient(access_token)

    try:
        identity = client.get_identity()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            console.print("[red]Invalid access token. Run `aw-import-timely auth` to get one.[/red]")
        else:
            console.print(f"[red]API error: {e}[/red]")
        raise typer.Exit(1)

    if account_id is None:
        # Identity returns something like {"account": 123, "user": 456}
        account_id_raw = identity.get("account")
        if account_id_raw is None:
            console.print("[red]Could not determine account ID from identity. Pass --account-id.[/red]")
            raise typer.Exit(1)
        account_id = int(account_id_raw)

    return client, account_id


# ── Auth command ──────────────────────────────────────────────────────

AUTH_PORT = 8321


@app.command()
def auth(
    client_id: str = typer.Option(..., prompt=True, help="Timely OAuth client ID"),
    client_secret: str = typer.Option(
        ..., prompt=True, hide_input=True, help="Timely OAuth client secret"
    ),
) -> None:
    """Authenticate with Timely via OAuth 2.0.

    Steps:
    1. Create an OAuth app at https://app.timelyapp.com/{account_id}/oauth_applications
       with redirect URI http://localhost:8321/callback
    2. Run this command with your client ID and secret.
    3. A browser window will open for authorization.
    4. The access token is printed — save it as TIMELY_ACCESS_TOKEN.
    """
    import http.server
    import json
    import threading
    import urllib.parse
    import webbrowser

    redirect_uri = f"http://localhost:{AUTH_PORT}/callback"

    authorize_url = (
        f"{TIMELY_API}/oauth/authorize"
        f"?response_type=code"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&client_id={urllib.parse.quote(client_id)}"
    )

    token_data: dict = {}
    event = threading.Event()

    class AuthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/callback" and "code" in qs:
                code = qs["code"][0]
                # Exchange code for token
                try:
                    resp = requests.post(
                        f"{TIMELY_API}/oauth/token",
                        data={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "redirect_uri": redirect_uri,
                            "code": code,
                            "grant_type": "authorization_code",
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                    token_data.update(resp.json())
                except requests.RequestException as e:
                    self._respond(
                        400,
                        f"<html><body><h2>Token exchange failed</h2><pre>{e}</pre></body></html>",
                    )
                    event.set()
                    return

                access_token = token_data.get("access_token", "")
                self._respond(
                    200,
                    "<html><body><h2>✓ Authenticated!</h2>"
                    "<p>You can close this window.</p>"
                    f"<p>Access token: <code>{access_token}</code></p>"
                    "</body></html>",
                )
                event.set()
            else:
                self._respond(400, "<html><body><h2>Missing authorization code</h2></body></html>")
                event.set()

        def _respond(self, status: int, body: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # quiet

    server = http.server.HTTPServer(("localhost", AUTH_PORT), AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    console.print("[bold]Opening browser for Timely authorization...[/bold]")
    console.print(f"Authorize URL: {authorize_url}")
    webbrowser.open(authorize_url)
    console.print("Waiting for authorization callback...")

    if not event.wait(timeout=120):
        console.print("[red]Timed out waiting for authorization.[/red]")
        raise typer.Exit(1)

    server.shutdown()

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token:
        console.print("[red]No access token received.[/red]")
        raise typer.Exit(1)

    console.print("\n[green]✓ Authentication successful![/green]")
    console.print("\nSave this token and use it with the import commands:")
    console.print(f"  [bold]export TIMELY_ACCESS_TOKEN={access_token}[/bold]")
    if refresh_token:
        console.print(f"\nRefresh token: {refresh_token}")


# ── Commands ──────────────────────────────────────────────────────────


@app.command()
def preview(
    access_token: str = typer.Option(
        ..., envvar="TIMELY_ACCESS_TOKEN", help="Timely OAuth access token"
    ),
    account_id: Optional[int] = typer.Option(
        None, help="Account ID (auto-detected if omitted)"
    ),
    start: Optional[str] = typer.Option(
        None, help="Start date filter (ISO 8601, e.g. 2024-01-01)"
    ),
    end: Optional[str] = typer.Option(None, help="End date filter (ISO 8601)"),
    limit: int = typer.Option(20, help="Max entries to show"),
) -> None:
    """Preview Timely time entries without importing."""
    client, aid = _resolve_account(access_token, account_id)

    params: dict[str, object] = {}
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    console.print("[dim]Fetching projects and clients...[/dim]")
    projects = client.get_projects(aid)
    clients = client.get_clients(aid)

    console.print("[dim]Fetching time entries...[/dim]")
    entries = client.get_hours(aid, params)
    events = build_events(entries, projects, clients)

    if not events:
        console.print("[yellow]No time entries found for the given filters.[/yellow]")
        raise typer.Exit()

    console.print(f"\nFound [bold]{len(events)}[/bold] importable time entries.\n")

    table = Table(title=f"Preview (first {min(limit, len(events))})")
    table.add_column("Date", style="cyan")
    table.add_column("Duration", style="green")
    table.add_column("Project", style="yellow")
    table.add_column("Client", style="magenta")
    table.add_column("Note")
    table.add_column("Billable", style="blue")

    for ev in events[:limit]:
        ts = datetime.fromisoformat(ev["timestamp"])
        table.add_row(
            ts.strftime("%Y-%m-%d"),
            _format_duration(ev["duration"]),
            ev["data"]["project"],
            ev["data"]["client"] or "[dim]-[/dim]",
            ev["data"]["title"] or "[dim](no note)[/dim]",
            "✓" if ev["data"]["billable"] else "",
        )

    console.print(table)


@app.command()
def import_data(
    access_token: str = typer.Option(
        ..., envvar="TIMELY_ACCESS_TOKEN", help="Timely OAuth access token"
    ),
    account_id: Optional[int] = typer.Option(
        None, help="Account ID (auto-detected if omitted)"
    ),
    start: Optional[str] = typer.Option(
        None, help="Start date filter (ISO 8601, e.g. 2024-01-01)"
    ),
    end: Optional[str] = typer.Option(None, help="End date filter (ISO 8601)"),
    aw_host: str = typer.Option(
        "http://localhost:5600", help="ActivityWatch server URL"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Import Timely time entries into ActivityWatch."""
    client, aid = _resolve_account(access_token, account_id)

    params: dict[str, object] = {}
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    console.print("[dim]Fetching projects and clients...[/dim]")
    projects = client.get_projects(aid)
    clients = client.get_clients(aid)

    console.print("[dim]Fetching time entries...[/dim]")
    entries = client.get_hours(aid, params)
    events = build_events(entries, projects, clients)

    if not events:
        console.print("[yellow]No time entries found. Nothing to import.[/yellow]")
        raise typer.Exit()

    console.print(
        f"\nReady to import [bold]{len(events)}[/bold] time entries into ActivityWatch."
    )
    console.print(f"Bucket: [bold]{BUCKET_ID}[/bold]")

    if not yes:
        typer.confirm("Proceed with import?", abort=True)

    console.print("[dim]Pushing events to ActivityWatch...[/dim]")
    try:
        _push_to_aw(events, aw_host)
    except requests.ConnectionError:
        console.print(f"[red]Cannot connect to ActivityWatch at {aw_host}.[/red]")
        console.print("Make sure ActivityWatch is running.")
        raise typer.Exit(1)
    except requests.HTTPError as e:
        console.print(f"[red]AW API error: {e}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓ Imported {len(events)} events into bucket '{BUCKET_ID}'[/green]"
    )
