# ClaimBot

A Discord bot for interest-signup boards and GitHub issue management.

## Features

- `/claim create` creates a board with multiple clickable options.
- Any number of members can sign up for each option.
- Members can sign up for as many different options as they want.
- Clicking an option again removes that member's sign-up.
- `/claim show-results` lists the members interested in every option.
- `/claim list-all-boards` privately lists every board in the server.
- SQLite stores boards and sign-ups across restarts.
- `/claim reset` removes every sign-up on a board.
- `/claim lock-claim` closes a claim and disables its sign-up buttons.
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

The bot loads `.env` automatically. You can also set environment variables directly. A Windows PowerShell example is:

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
- list-all-boards
- reset
- lock-claim
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
GUI tester (1 interested): @Arthur
```

All buttons remain active. Other members can sign up for the same option, and Arthur can click again to remove his sign-up.

## 6. Results

Run:

```text
/claim show-results name:test-1
```

The response shows everyone interested in each option, across multiple messages when needed.

## 7. Railway

For Railway, set `DISCORD_TOKEN` and `COMMAND_ROLE_ID` as Railway environment variables.

If you want SQLite data to survive redeploys/restarts, attach a Railway Volume and set:

```text
DB_PATH=/data/claims.db
```

where `/data` is the mount path of your volume.

Do not commit `.env` or `claims.db` to GitHub.

## 8. GitHub integration

The optional `/issue` portion manages GitHub issues and relays repository activity into a Discord text channel. It defaults to `SirArthur2009/discord-bot`, this project's GitHub remote. Set `GITHUB_REPO=owner/repository` to use another repository.

### Configuration

For a single-server setup, set these environment variables in `.env` or Railway:

```text
GITHUB_REPO=SirArthur2009/discord-bot
GITHUB_TOKEN=your_fine_grained_token
GITHUB_CHANNEL_ID=your_discord_text_channel_id
GITHUB_WEBHOOK_SECRET=your_random_webhook_secret
```

Leave the token, channel, and secret blank to run only the claim portion. If you configure one, configure all three. The activity channel determines which Discord server can run `/issue` commands. All issue management commands require `COMMAND_ROLE_ID`; members with access to a linked discussion can reply without that role.

### Two or more Discord servers

Use the same bot installation and `DISCORD_TOKEN` for both servers. Invite the bot into each server, then copy `servers.example.json` to `servers.json`. Replace the example IDs with each server's actual IDs:

```json
[
  {
    "guild_id": "FIRST_SERVER_ID",
    "command_role_id": "FIRST_SERVER_ROLE_ID",
    "github_channel_id": "FIRST_SERVER_ACTIVITY_CHANNEL_ID",
    "github_repo": "SirArthur2009/discord-bot"
  },
  {
    "guild_id": "SECOND_SERVER_ID",
    "command_role_id": "SECOND_SERVER_ROLE_ID",
    "github_channel_id": "SECOND_SERVER_ACTIVITY_CHANNEL_ID",
    "github_repo": "SirArthur2009/discord-bot"
  }
]
```

Set `SERVER_CONFIG_PATH=servers.json` in `.env` or Railway. The file must be included in the deployment at that path. Server, role, and channel IDs can be copied with Discord's Developer Mode enabled. Roles have different IDs in each server even when their names match.

The file overrides `GUILD_ID`, `COMMAND_ROLE_ID`, `GITHUB_CHANNEL_ID`, and `GITHUB_REPO`. Commands sync directly to every listed server, and both `/claim` and `/issue` use that server's `command_role_id`. Unlisted servers cannot run management commands. Omit `github_channel_id` from a server's entry if it should have only claim boards.

Keep `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` in the environment. They are shared by the configured integrations; no token or secret belongs in `servers.json`. The token needs access to all configured repositories.

Both servers may use the same repository or different repositories. For a shared repository, create one GitHub webhook using the URL below; each delivery is sent to both configured activity channels. For different repositories, create a webhook in each repository with the same URL and secret. Threads, comment copies, deadlines, reminder records, and username links are separate for each server. A Discord reply is posted to GitHub once and appears in the other server through the GitHub webhook. Changes to GitHub issue state and labels affect the shared repository, so completing an issue also cancels reminders in the other server once its webhook is processed or its reminder check sees the closed issue.

When migrating an existing single-server deployment, keep its original `GITHUB_CHANNEL_ID` and `GITHUB_REPO` environment values for the first restart and include that channel in the file. Its existing thread mappings, pending jobs, and deadlines are moved into that channel's storage scope automatically. The settings in the file still control routing.

Create a fine-grained GitHub token limited to the chosen repository with **Issues: read/write** and **Pull requests: read/write**. GitHub automatically includes Metadata access; Contents permission is unnecessary. The token owner must have repository access. Priority labels are created automatically as `priority:low`, `priority:medium`, `priority:high`, and `priority:urgent`.

In the Discord Developer Portal, enable the bot's **Message Content Intent** so replies can be read. In the activity channel, grant the bot **View Channel**, **Send Messages**, **Embed Links**, **Read Message History**, **Create Public Threads**, **Send Messages in Threads**, **Attach Files**, and **Manage Threads**. The bot reopens archived threads when activity arrives. Members who will comment need access to the activity channel and its threads.

On Railway, generate a public HTTPS domain for the service, routed to the automatically supplied `PORT`. Keep a persistent volume and `DB_PATH=/data/claims.db`. Run one instance of the bot; the SQLite job queue and Discord gateway connection are designed for a single worker.

In the GitHub repository's **Settings → Webhooks**, add:

- Payload URL: `https://YOUR_PUBLIC_DOMAIN/github/webhook`
- Content type: `application/json`
- Secret: the exact value of `GITHUB_WEBHOOK_SECRET`
- Events: **Issues**, **Pull requests**, **Issue comments**, **Pushes**, **Pull request reviews**, and **Pull request review comments**

Restart the bot after configuration so `/issue` commands sync. Confirm GitHub's webhook ping returns HTTP 202. The `/health` endpoint returns 200 when Discord is ready, otherwise 503. For local development, the webhook server listens on `PORT` or 8080; GitHub needs a reachable HTTPS endpoint or tunnel.

Webhook signatures are verified before anything is queued, and deliveries for other repositories are rejected. Accepted deliveries, thread mappings, comment mappings, and reminders live in SQLite and survive restarts. Failed relay jobs retry with backoff; duplicate delivery IDs are ignored. Check the bot logs and GitHub's delivery history if an event is missing.

### Issue commands

| Command | Purpose |
| --- | --- |
| `/issue create title description priority assignee` | Create an issue, choose its priority, and optionally assign a linked Discord member. |
| `/issue link username` | Set your GitHub username for assignment and reminder routing. |
| `/issue unlink` | Disconnect your GitHub sign-in and remove your username preference. |
| `/issue assign number member` | Add a linked member as an assignee. |
| `/issue unassign number member` | Remove a linked member as an assignee. |
| `/issue priority number level` | Set low, medium, high, or urgent priority without removing unrelated labels. |
| `/issue complete number` | Close the issue as completed and stop its deadline reminders. |
| `/issue reopen number` | Reopen an issue; set a new deadline if needed. |
| `/issue show number` | Show an existing issue or PR and create/update its discussion thread. |
| `/issue list state page` | List open, closed, or all issues, 25 per page. |
| `/issue deadline number due recipient` | Set an issue deadline; recipient defaults to the person running the command. |

Assignment, priority, completion, and reopening modify GitHub directly. PRs are relayed and can be discussed, but `/issue complete` cannot merge or close a PR. Use GitHub for PR management. Username links are self-declared routing preferences, not verified GitHub authentication.

### Activity and replies

Issue and PR events create or refresh an activity card with a discussion thread. Pushes post commit summaries and a comparison link. Reviews and inline review comments appear in the PR thread. Use `/issue show` to start discussing an existing issue or PR; the bot does not import historical events or comments automatically.

Messages from server members inside an issue/PR thread become GitHub issue comments. Direct Discord replies to the activity card also become comments. Other messages in the activity channel are not forwarded. GitHub issue comments (including general PR conversation comments) appear in the corresponding Discord thread. Bot/webhook messages are ignored, and forwarded comment echoes are suppressed.

Comments and newly created issues use the member's GitHub App user token when they have signed in. Otherwise they use the owner's configured GitHub token. By default, every distinct fallback post triggers a DM asking the author to sign in; retries do not repeat that DM. Members can disable these reminders with `/github reminders enabled:false`. Closed DMs do not prevent posting. Comments include the Discord author's name and original-message link. User IDs are omitted from the visible author header. Discord tags become GitHub `@username` mentions when a username is linked; otherwise they become clickable `@display-name` links to Discord. Attachment URLs are included; files are not copied to GitHub and Discord URLs may expire. Edits and deletions retain the original comment's credential owner, even if the author later signs in. GitHub-origin comment edits/deletions update/remove the bot's Discord copy. GitHub edits to a Discord-authored comment cannot rewrite the original user's Discord message. Inline PR review replies sent from Discord become general PR conversation comments.

Long GitHub comments are attached as Markdown files so the full text remains available. GitHub activity does not trigger Discord mentions. Delivery is retried after failures; a crash immediately after a Discord send or DM can cause a duplicate notification.

PR merge updates are sent silently: they remain visible in the discussion thread without a push notification. Separate GitHub push events still post normal commit summaries. To mute more activity, right-click the GitHub activity channel (or press and hold it on mobile) and choose **Notification Settings** → **Nothing** or **Mute Channel**. For an individual PR, open its thread's notification settings and choose **Nothing**. Server-wide controls are under the server menu → **Notification Settings**; app-wide push controls are in **User Settings** → **Notifications**.

To stop DMs about posting without a GitHub sign-in, run `/github reminders enabled:false` in the relevant Discord server. Run `/github reminders enabled:true` to restore them. This preference is saved across restarts, applies only to you in that server, and does not change posting, sign-in confirmations, or deadline reminders. Each sign-in reminder DM includes the opt-out command; `/github status` also shows your current preference.

### GitHub sign-in

Register a GitHub App with **Device Flow** enabled, expiring user tokens enabled, **Issues: read/write**, **Pull requests: read/write**, and mandatory **Metadata: read**. Leave all other permissions off. Make the app public so other members can authorize it; this does not make the repository public. Install it only on the repository configured for the Discord server. The existing repository webhook handles activity; the app's webhook can remain inactive. A device flow does not require a client secret or private key.

Set `GITHUB_APP_CLIENT_ID` and `GITHUB_AUTH_ENCRYPTION_KEY` in `.env` and Railway. Generate the encryption key once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep the key secret and stable across redeploys, and back it up separately from the database. User access/refresh tokens and pending device codes are encrypted in SQLite. Tokens refresh automatically; expired or revoked sign-ins fall back to the owner. Pending sign-ins survive restarts and obey GitHub's polling interval.

Members can use `/github login` without the command role, or click **Sign in to GitHub** in a reminder DM. The bot gives them a private code to enter at GitHub's device sign-in page. After authorization, it verifies their GitHub identity and repository access, then saves the verified username for assignments and mentions. Each user must have access to the private repository; authorizing the app does not grant repository membership. `/github status` shows their connection, and `/github logout` removes locally stored credentials and pending sign-ins. Users can also revoke authorization in GitHub Settings → Applications. `/issue link` remains a username preference and cannot authenticate an account.

Only Discord servers with GitHub integration receive `/github` commands. Issue management commands still require the configured command role. Automated activity reads, labels, assignments, issue state changes, and deadlines continue to use the owner's token.

### Deadlines and DMs

Use a timestamp with an explicit UTC offset, for example:

```text
/issue deadline number:7 due:2026-10-20T17:00:00-06:00 recipient:@Arthur
```

Discord renders dates in each member's local timezone. Deadlines are stored in the bot's SQLite database; GitHub has no per-issue due-date field managed by this bot. The activity card displays the deadline.

The bot checks deadlines every minute and DMs the chosen recipient plus current GitHub assignees with linked Discord usernames. Recipients must still belong to the server and be able to view the activity channel. It sends one reminder in each applicable window: within 24 hours, within one hour, and once overdue. If a deadline is set close to its due time, or the bot was offline, it sends the current window's reminder rather than every missed reminder.

Use `/issue deadline number:7 due:clear` to remove a deadline. Changing a deadline resets its reminders. Completing/closing the issue cancels reminders; reopening requires a new deadline. Members must allow DMs from the bot/server. Disabled DMs are recorded in the logs and skipped for that reminder window.

## 9. Local checks

After installing dependencies, run:

```bash
python -m unittest -v
```

Tests use temporary databases, an in-process webhook server, and mocked GitHub/Discord operations. They do not read local credentials, connect the bot to Discord, or change GitHub issues.
