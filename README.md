# ClaimBot

A Discord bot for first-come, first-served claim boards.

## Features

- `/claim create` creates a board with multiple clickable options.
- The first person to claim an option wins that option; each member can claim only one option per board.
- Each claimed button becomes disabled while other options remain available.
- SQLite stores claims, so restarts do not lose winners.
- `/claim show-results` shows claimed and unclaimed options.
- `/claim reset` releases every option on a board.
- `/claim delete` removes a board.
- All `/claim` commands require the role configured with `COMMAND_ROLE_ID`.

## 1. Create the Discord app

Open the Discord Developer Portal and create a new application.

Add a bot user, then copy its bot token. **Never put the token into GitHub or share it.**

For server installation, the app needs the `bot` and `applications.commands` scopes. The bot needs at least:

- View Channels
- Send Messages
- Embed Links

Discord's current documentation describes server installation and these scopes/permissions:
https://docs.discord.com/developers/quick-start/getting-started

## 2. Install Python dependencies

Python 3.10+ is recommended.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
```

## 3. Configure the bot

Copy `.env.example` to `.env`.

Set `DISCORD_TOKEN` and `COMMAND_ROLE_ID` (the numeric ID of the Discord role allowed to run `/claim` commands).

For local development, you can also set `GUILD_ID` to your test server ID. Guild-scoped slash commands appear much faster than global commands.

The program itself reads environment variables, so if you use a `.env` file locally, either export those variables in your shell or install/use a dotenv loader. An easy Windows PowerShell example is:

```powershell
$env:DISCORD_TOKEN="YOUR_TOKEN"
$env:GUILD_ID="YOUR_SERVER_ID"
$env:COMMAND_ROLE_ID="YOUR_ALLOWED_ROLE_ID"
python bot.py
```

## 4. Start the bot

```bash
python bot.py
```

You should see:

```text
Synced commands to guild ...
Logged in as ...
```

Then Discord should show:

```text
/claim
```

with:

- create
- show-results
- reset
- delete

## 5. Create a board

Use:

```text
/claim create
```

Fill in:

```text
name: test-1
message: We need people to help test the new update!
options: bug tester, GUI tester, graphics tester
```

The bot will post a board with three buttons.

If Arthur clicks `GUI tester`, that option becomes:

```text
🔒 GUI Tester — @Arthur
```

while the other buttons remain active.

## 6. Results

Run:

```text
/claim show-results name:test-1
```

The response will show who claimed each option and which options remain available.

## 7. Railway

For Railway, set `DISCORD_TOKEN` as a Railway environment variable.

If you want SQLite data to survive redeploys/restarts, attach a Railway Volume and set:

```text
DB_PATH=/data/claims.db
```

where `/data` is the mount path of your volume.

Do not commit `.env` or `claims.db` to GitHub.
