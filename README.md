# aw-import-timely

Import [Timely](https://www.timely.com/) time entries into [ActivityWatch](https://activitywatch.net).

Timely is a time tracking tool with automatic time capture (Memory/AutoSheet) and manual
time entry. This tool migrates your historical Timely data into ActivityWatch so you can
view all your time data in one place.

**Part of the [ActivityWatch data portability hub](https://timetobuildbob.github.io/blog/aw-data-portability-hub-five-importers/).**

## What Gets Imported

Each Timely time entry (hour record) becomes an ActivityWatch event with:
- **title** — the entry note/description
- **project** — the project name
- **client** — the client name (if assigned)
- **billable** — whether the entry is billable
- **source** — `timely`

All events land in a single bucket: `aw-import-timely`.

## Installation

```sh
uv tool install git+https://github.com/TimeToBuildBob/aw-import-timely
```

Or with pip:
```sh
pip install git+https://github.com/TimeToBuildBob/aw-import-timely
```

## Authentication

Timely uses OAuth 2.0. You need to:

### 1. Create an OAuth application

1. Go to **Settings → Devs** in your Timely account
2. Click **Add application** and give it a name
3. Set the **Redirect URI** to `http://localhost:8321/callback`
4. Note the **Client ID** and **Client Secret**

### 2. Run the auth command

```sh
aw-import-timely auth --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
```

This will:
1. Open your browser for authorization
2. Start a local server to receive the callback
3. Print your access token

### 3. Set the token

```sh
export TIMELY_ACCESS_TOKEN="your-token-here"
```

## Usage

### Preview entries

```sh
# Preview all entries (last 20 shown)
aw-import-timely preview

# Preview from a specific date range
aw-import-timely preview --start 2025-01-01 --end 2025-12-31

# Preview with explicit account ID
aw-import-timely preview --account-id 12345
```

### Import into ActivityWatch

Make sure ActivityWatch is running, then:

```sh
# Import all entries (with confirmation prompt)
aw-import-timely import-data

# Import without confirmation
aw-import-timely import-data --yes

# Import a specific date range
aw-import-timely import-data --start 2024-01-01 --yes

# Import to a non-default AW host
aw-import-timely import-data --aw-host http://localhost:5600 --yes
```

## How It Works

1. Authenticates via OAuth 2.0 Bearer token
2. Resolves account ID from the identity endpoint
3. Fetches project and client names for reference
4. Fetches all hour entries from `/1.1/{account_id}/hours`
5. Converts to ActivityWatch events (start-of-day timestamp + duration)
6. Creates an ActivityWatch bucket `aw-import-timely`
7. Inserts all events in batches

## Development

```sh
git clone https://github.com/TimeToBuildBob/aw-import-timely.git
cd aw-import-timely
uv sync
uv run pytest
```

## License

MIT
