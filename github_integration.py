"""Issue commands, signed GitHub webhooks, comment relay, and deadline reminders."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

import github_store as store
from github_api import GitHubAPI, GitHubError, PRIORITIES

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass(frozen=True)
class GitHubSettings:
    token: str
    repo: str
    channel_id: int
    secret: str
    port: int = 8080
    guild_id: int | None = None
    scope: str = ""

    @classmethod
    def from_environment(cls):
        token = os.getenv("GITHUB_TOKEN", "")
        channel = os.getenv("GITHUB_CHANNEL_ID", "")
        secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
        if not any((token, channel, secret)):
            return None
        if not all((token, channel, secret)):
            raise RuntimeError("GitHub integration needs GITHUB_TOKEN, GITHUB_CHANNEL_ID, and GITHUB_WEBHOOK_SECRET.")
        repo = os.getenv("GITHUB_REPO", "SirArthur2009/discord-bot").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("GITHUB_REPO must be owner/repository.")
        if not channel.isdigit() or int(channel) <= 0:
            raise RuntimeError("GITHUB_CHANNEL_ID must be a positive numeric Discord text channel ID.")
        port = int(os.getenv("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise RuntimeError("PORT must be between 1 and 65535.")
        return cls(token, repo, int(channel), secret, port)


def valid_signature(body, secret, signature):
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (signature or "").encode())


def parse_deadline(value):
    try:
        due = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Use a date and time with an offset, e.g. 2026-10-20T17:00:00-06:00.") from error
    if due.tzinfo is None:
        raise ValueError("Include a timezone offset, such as -06:00 or Z.")
    due = due.astimezone(timezone.utc)
    if due <= datetime.now(timezone.utc):
        raise ValueError("The deadline must be in the future.")
    return due


def reminder_stage(due, now):
    seconds = (due - now).total_seconds()
    if seconds <= 0:
        return "overdue"
    if seconds <= 3600:
        return "1h"
    if seconds <= 86400:
        return "24h"
    return None


def issue_embed(issue, repo, storage_repo=None):
    kind = "PR" if "pull_request" in issue else "Issue"
    embed = discord.Embed(title=f"{kind} #{issue['number']}: {issue['title']}"[:256],
                          url=issue["html_url"], description=(issue.get("body") or "No description.")[:3500],
                          color=discord.Color.green() if issue["state"] == "open" else discord.Color.greyple())
    embed.add_field(name="Status", value=issue["state"], inline=True)
    embed.add_field(name="Assigned to", value=(", ".join(item["login"] for item in issue.get("assignees", [])) or "Unassigned")[:1024])
    labels = [item["name"] for item in issue.get("labels", [])]
    embed.add_field(name="Priority", value=next((name.split(":", 1)[1] for name in labels if name.startswith("priority:")), "Unset"))
    due = store.get_deadline(storage_repo or repo, issue["number"])
    if due:
        timestamp = int(datetime.fromisoformat(due["due_at"]).timestamp())
        embed.add_field(name="Deadline", value=f"<t:{timestamp}:F> (<t:{timestamp}:R>)", inline=False)
    embed.set_footer(text=f"{repo} · Replies in the thread sync to GitHub for members with the command role.")
    return embed


def comment_content(comment):
    return f"**{discord.utils.escape_markdown(comment['user']['login'])} on GitHub**\n{comment.get('body') or '(empty comment)'}"


def comment_kwargs(comment):
    content = comment_content(comment)
    if len(content) <= 2000:
        return {"content": content, "allowed_mentions": NO_MENTIONS}
    return {"content": f"GitHub comment by **{discord.utils.escape_markdown(comment['user']['login'])}** (full text attached).\n{comment['html_url']}",
            "file": discord.File(BytesIO(content.encode()), filename=f"comment-{comment['id']}.md"),
            "allowed_mentions": NO_MENTIONS}


class IssueCog(commands.Cog):
    issue_group = app_commands.Group(name="issue", description="Manage GitHub issues, discussions, and deadlines.", guild_only=True)

    def __init__(self, bot, settings, role_id):
        self.bot = bot
        self.settings = settings
        self.repo = settings.repo
        self.storage_repo = settings.scope or self.repo
        self.role_id = role_id
        self.api = None
        self.runner = None
        self.sync_lock = asyncio.Lock()

    async def cog_load(self, serve_http=True):
        store.init_github_db()
        self.api = GitHubAPI(self.settings.token, self.repo)
        if not serve_http:
            self.jobs.start()
            self.reminders.start()
            return
        app = web.Application(client_max_size=26 * 1024 * 1024)
        app.router.add_post("/github/webhook", self.webhook)
        app.router.add_get("/health", self.health)
        self.runner = web.AppRunner(app)
        try:
            await self.runner.setup()
            await web.TCPSite(self.runner, "0.0.0.0", self.settings.port).start()
        except Exception:
            await self.runner.cleanup()
            await self.api.close()
            raise
        self.jobs.start()
        self.reminders.start()
        log.info("GitHub integration enabled for %s on port %s", self.repo, self.settings.port)

    async def cog_unload(self):
        self.jobs.cancel()
        self.reminders.cancel()
        pending = [task for task in (self.jobs.get_task(), self.reminders.get_task()) if task]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
        if self.api:
            await self.api.close()

    async def health(self, request):
        return web.json_response({"ready": self.bot.is_ready()}, status=200 if self.bot.is_ready() else 503)

    async def webhook(self, request):
        body = await request.read()
        if not valid_signature(body, self.settings.secret, request.headers.get("X-Hub-Signature-256")):
            raise web.HTTPUnauthorized(text="Invalid signature")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Expected an object")
        repository = payload.get("repository") or {}
        if not isinstance(repository, dict) or repository.get("full_name", "").casefold() != self.repo.casefold():
            raise web.HTTPForbidden(text="Unexpected repository")
        event = request.headers.get("X-GitHub-Event", "")
        delivery = request.headers.get("X-GitHub-Delivery", "")
        if not delivery or len(delivery) > 200:
            raise web.HTTPBadRequest(text="Missing or invalid delivery ID")
        if event in {"issues", "pull_request", "issue_comment", "push", "pull_request_review", "pull_request_review_comment"}:
            store.enqueue(f"github:{delivery}", event, payload, self.storage_repo)
        return web.Response(status=202)

    async def channel(self):
        channel = self.bot.get_channel(self.settings.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.settings.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("GITHUB_CHANNEL_ID must identify a server text channel.")
        if self.settings.guild_id is not None and channel.guild.id != self.settings.guild_id:
            raise ValueError("The GitHub activity channel does not belong to its configured Discord server.")
        return channel

    async def interaction_check(self, interaction):
        if not isinstance(interaction.user, discord.Member) or not any(role.id == self.role_id for role in interaction.user.roles):
            raise app_commands.CheckFailure("Required role missing")
        await interaction.response.defer(ephemeral=True)
        channel = await self.channel()
        if interaction.guild_id != channel.guild.id:
            await interaction.followup.send("GitHub commands are available only in the server with the GitHub activity channel.", ephemeral=True)
            return False
        return True

    async def cog_app_command_error(self, interaction, error):
        original = getattr(error, "original", error)
        if isinstance(original, app_commands.CheckFailure):
            text = "You need the configured command role to use /issue."
        elif isinstance(original, (GitHubError, ValueError)):
            text = str(original)
        else:
            log.error("Issue command failed", exc_info=(type(original), original, original.__traceback__))
            text = "The operation failed. Check the bot logs before retrying; a GitHub change may already have succeeded."
        if interaction.response.is_done():
            await interaction.followup.send(text[:1900], ephemeral=True)
        else:
            await interaction.response.send_message(text[:1900], ephemeral=True)
        interaction.extras["github_error_handled"] = True

    async def ensure_thread(self, number, issue=None):
        """Caller holds sync_lock to keep slash commands and webhook deliveries consistent."""
        issue = issue or await self.api.issue(number)
        channel = await self.channel()
        row = store.get_thread(self.storage_repo, number)
        message = None
        if row:
            try:
                message = await channel.fetch_message(row["message_id"])
            except discord.NotFound:
                pass
        if message is None:
            message = await channel.send(embed=issue_embed(issue, self.repo, self.storage_repo), allowed_mentions=NO_MENTIONS)
            # Persist the root before creating its thread so a retry reuses it.
            store.save_thread(self.storage_repo, number, message.id)
        else:
            await message.edit(embed=issue_embed(issue, self.repo, self.storage_repo), allowed_mentions=NO_MENTIONS)
        row = store.get_thread(self.storage_repo, number)
        thread = self.bot.get_channel(row["thread_id"] or message.id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(row["thread_id"] or message.id)
            except discord.NotFound:
                thread = await message.create_thread(name=f"#{number} {issue['title']}"[:100], auto_archive_duration=1440)
        store.save_thread(self.storage_repo, number, message.id, thread.id)
        if thread.archived:
            await thread.edit(archived=False)
        return thread

    async def publish(self, issue):
        async with self.sync_lock:
            return await self.ensure_thread(issue["number"], issue)

    @tasks.loop(seconds=2)
    async def jobs(self):
        job = store.next_job(self.storage_repo)
        if job is None:
            return
        try:
            async with self.sync_lock:
                await self.process_event(job["event"], json.loads(job["payload"]))
            store.finish_job(job["id"])
        except Exception as error:
            store.retry_job(job, getattr(error, "retry_after", None))
            log.warning("GitHub relay job %s failed (attempt %s): %s", job["id"], job["attempts"] + 1, error)

    @jobs.before_loop
    async def before_jobs(self):
        await self.bot.wait_until_ready()

    async def process_event(self, event, payload):
        if event == "discord_comment":
            await self.forward_discord_comment(payload)
            return
        if event in {"discord_comment_edit", "discord_comment_delete"}:
            await self.update_discord_comment(event, payload)
            return
        if event == "push":
            channel = await self.channel()
            lines = [f"[`{commit['id'][:7]}`]({commit['url']}) {discord.utils.escape_markdown((commit['message'].splitlines() or ['(no message)'])[0])}"
                     for commit in payload.get("commits", [])[:10]]
            description = "\n".join(lines) or "Branch or tag updated."
            if len(payload.get("commits", [])) > 10:
                description += "\nSee the comparison for remaining commits."
            embed = discord.Embed(title=f"Push to {payload['ref']}"[:256], description=description[:4000],
                                  url=payload.get("compare"), color=discord.Color.blurple())
            embed.set_footer(text=f"{self.repo} · {payload.get('sender', {}).get('login', 'GitHub')}")
            await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
            return
        issue = payload.get("issue") or payload.get("pull_request")
        if not issue:
            return
        # Fetch current state rather than rolling the card back for delayed deliveries.
        current = await self.api.issue(issue["number"])
        thread = await self.ensure_thread(issue["number"], current)
        if event in {"issues", "pull_request"}:
            if current["state"] == "closed":
                store.delete_deadline(self.storage_repo, issue["number"])
                # Refresh once more to remove the cancelled deadline from the card.
                await self.ensure_thread(issue["number"], current)
            await thread.send(f"{event.replace('_', ' ').title()}: **{payload.get('action', 'updated')}** by "
                              f"{discord.utils.escape_markdown(payload.get('sender', {}).get('login', 'GitHub'))}.",
                              allowed_mentions=NO_MENTIONS)
        elif event == "issue_comment":
            await self.relay_github_comment(thread, payload)
        elif event in {"pull_request_review", "pull_request_review_comment"}:
            item = payload.get("review") or payload.get("comment") or {}
            if payload.get("action") in {"submitted", "created"}:
                text = f"PR review by **{discord.utils.escape_markdown(item.get('user', {}).get('login', 'GitHub'))}**: {item.get('state', 'comment')}\n{item.get('body') or ''}\n{item.get('html_url') or current['html_url']}"
                await thread.send(text[:2000], allowed_mentions=NO_MENTIONS)

    async def relay_github_comment(self, thread, payload):
        comment = payload["comment"]
        row = store.get_comment(self.storage_repo, comment["id"])
        if row and row["source"] == "discord":
            return  # Acknowledge echoes without forwarding the author's own message back.
        action = payload["action"]
        if action == "deleted":
            if row:
                try:
                    await thread.get_partial_message(row["message_id"]).delete()
                except discord.NotFound:
                    pass
            return
        if action not in {"created", "edited"}:
            return
        try:
            # Use the latest body if created/edited deliveries arrive out of order.
            comment = await self.api.repo_request("GET", f"issues/comments/{comment['id']}")
        except GitHubError as error:
            if error.status == 404:
                return  # The comment was deleted before this delivery was processed.
            raise
        if row:
            try:
                kwargs = comment_kwargs(comment)
                if "file" in kwargs:
                    kwargs["attachments"] = [kwargs.pop("file")]
                else:
                    kwargs["attachments"] = []
                await thread.get_partial_message(row["message_id"]).edit(**kwargs)
                return
            except discord.NotFound:
                pass
        sent = await thread.send(**comment_kwargs(comment))
        store.save_comment(self.storage_repo, comment["id"], sent.id, "github")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.webhook_id:
            return
        if isinstance(message.channel, discord.Thread) and message.channel.parent_id == self.settings.channel_id:
            row = store.get_thread_by_id(self.storage_repo, message.channel.id)
        elif message.channel.id == self.settings.channel_id and message.reference:
            row = store.get_thread_by_message(self.storage_repo, message.reference.message_id)
        else:
            return
        if not row or not isinstance(message.author, discord.Member):
            return
        if not any(role.id == self.role_id for role in message.author.roles):
            return
        if not message.content and not message.attachments:
            return
        # Persist before contacting GitHub. A failed request is retried after restart.
        store.enqueue(f"discord:{message.id}", "discord_comment", {
            "number": row["number"], "message_id": message.id, "thread_id": message.channel.id,
            "guild_id": message.guild.id, "user_id": message.author.id,
            "author": message.author.display_name, "content": message.content,
            "attachments": [attachment.url for attachment in message.attachments],
        }, self.storage_repo)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, event):
        # Discord emits updates for embeds as well as text; avoid forwarding link-preview updates.
        if "content" not in event.data and "attachments" not in event.data:
            return
        row = store.get_comment_by_message(self.storage_repo, event.message_id)
        thread_row = store.get_thread_by_id(self.storage_repo, event.channel_id)
        # Also accept edits while the initial comment is still queued.
        pending = store.pending_discord_comment(self.storage_repo, event.message_id)
        if (not thread_row and event.channel_id != self.settings.channel_id) or (row and row["source"] != "discord"):
            return
        if not row and not pending:
            return
        payload = {"message_id": event.message_id, "thread_id": event.channel_id}
        revision = event.data.get("edited_timestamp") or hashlib.sha256(json.dumps(event.data, sort_keys=True).encode()).hexdigest()
        store.enqueue(f"discord-edit:{event.message_id}:{revision}", "discord_comment_edit", payload, self.storage_repo)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, event):
        row = store.get_comment_by_message(self.storage_repo, event.message_id)
        pending = store.pending_discord_comment(self.storage_repo, event.message_id)
        if (store.get_thread_by_id(self.storage_repo, event.channel_id) or event.channel_id == self.settings.channel_id) and (pending or (row and row["source"] == "discord")):
            store.enqueue(f"discord-delete:{event.message_id}", "discord_comment_delete", {"message_id": event.message_id}, self.storage_repo)

    async def update_discord_comment(self, event, payload):
        row = store.get_comment_by_message(self.storage_repo, payload["message_id"])
        if not row or row["source"] != "discord":
            # A creation may be waiting on a transient failure. Preserve edit/delete order.
            pending = store.pending_discord_comment(self.storage_repo, payload["message_id"])
            if pending:
                raise ValueError("Initial Discord comment is still pending; retrying its update later.")
            return
        path = f"issues/comments/{row['comment_id']}"
        if event == "discord_comment_delete":
            try:
                await self.api.repo_request("DELETE", path)
            except GitHubError as error:
                if error.status != 404:
                    raise
            return
        channel = self.bot.get_channel(payload["thread_id"]) or await self.bot.fetch_channel(payload["thread_id"])
        if channel.id != self.settings.channel_id and (not isinstance(channel, discord.Thread) or channel.parent_id != self.settings.channel_id):
            return
        try:
            message = await channel.fetch_message(payload["message_id"])
        except discord.NotFound:
            return
        if message.author.bot:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            try:
                member = await channel.guild.fetch_member(message.author.id)
            except discord.NotFound:
                return
        if not any(role.id == self.role_id for role in member.roles):
            return
        # Read the latest revision so a delayed edit job cannot overwrite a newer edit.
        payload = {**payload, "content": message.content,
                   "attachments": [attachment.url for attachment in message.attachments]}
        try:
            comment = await self.api.repo_request("GET", path)
        except GitHubError as error:
            if error.status == 404:
                return
            raise
        original = comment["body"]
        # Preserve attribution and the original link/marker while replacing the message body.
        header, _, remainder = original.partition("\n\n")
        _, _, footer = remainder.rpartition("\n\n[Original Discord message]")
        if not footer:
            raise ValueError("Cannot update a comment whose Discord attribution was removed on GitHub.")
        content = payload["content"]
        if payload["attachments"]:
            content += "\n\nAttachments:\n" + "\n".join(payload["attachments"])
        body = f"{header}\n\n{content}\n\n[Original Discord message]{footer}"
        await self.api.repo_request("PATCH", path, json={"body": body})

    async def forward_discord_comment(self, payload):
        if store.get_comment_by_message(self.storage_repo, payload["message_id"]):
            return
        marker = f"<!-- claimbot-discord:{payload['message_id']} -->"
        # Recover from a crash after GitHub accepted a POST but before SQLite recorded it.
        async for comment in self.api.comments(payload["number"]):
            if (comment.get("body") or "").endswith(marker):
                store.save_comment(self.storage_repo, comment["id"], payload["message_id"], "discord")
                return
        author = discord.utils.escape_markdown(payload["author"])
        body = f"**{author} on Discord** (user ID {payload['user_id']})\n\n{payload['content']}"
        if payload["attachments"]:
            body += "\n\nAttachments:\n" + "\n".join(payload["attachments"])
        body += f"\n\n[Original Discord message](https://discord.com/channels/{payload['guild_id']}/{payload['thread_id']}/{payload['message_id']})\n\n{marker}"
        comment = await self.api.repo_request("POST", f"issues/{payload['number']}/comments", json={"body": body})
        store.save_comment(self.storage_repo, comment["id"], payload["message_id"], "discord")

    @tasks.loop(seconds=60)
    async def reminders(self):
        for deadline in store.all_deadlines(self.storage_repo):
            try:
                await self.send_reminders(deadline)
            except Exception as error:
                log.warning("Deadline reminder for #%s failed: %s", deadline["number"], error)

    @reminders.before_loop
    async def before_reminders(self):
        await self.bot.wait_until_ready()

    async def send_reminders(self, deadline):
        due = datetime.fromisoformat(deadline["due_at"])
        stage = reminder_stage(due, datetime.now(timezone.utc))
        if stage is None:
            return
        issue = await self.api.issue(deadline["number"])
        if issue["state"] == "closed":
            store.delete_deadline(self.storage_repo, deadline["number"])
            return
        recipients = store.linked_assignees(deadline["guild_id"], [user["login"] for user in issue.get("assignees", [])])
        recipients.add(deadline["recipient_id"])
        channel = await self.channel()
        guild = channel.guild
        if guild.id != deadline["guild_id"]:
            return
        for user_id in recipients:
            if store.reminder_sent(deadline, user_id, stage):
                continue
            try:
                member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                if not channel.permissions_for(member).view_channel:
                    continue
                # Avoid delivering reminders for a deadline replaced during the API request.
                current = store.get_deadline(self.storage_repo, deadline["number"])
                if not current or current["due_at"] != deadline["due_at"]:
                    return
                await member.send(f"{'Overdue' if stage == 'overdue' else 'Upcoming deadline'}: "
                                  f"**#{issue['number']} {issue['title']}**\n"
                                  f"Server: {getattr(guild, 'name', str(guild.id))}\n"
                                  f"Due <t:{int(due.timestamp())}:F> (<t:{int(due.timestamp())}:R>)\n{issue['html_url']}",
                                  allowed_mentions=NO_MENTIONS)
                store.mark_reminder(deadline, user_id, stage)
            except discord.NotFound:
                continue
            except discord.Forbidden:
                # Log once per stage, rather than retrying closed DMs every minute.
                store.mark_reminder(deadline, user_id, stage)
                log.warning("Cannot DM deadline #%s to Discord user %s; DMs are disabled.", deadline["number"], user_id)

    async def require_issue(self, number):
        issue = await self.api.issue(number)
        if "pull_request" in issue:
            raise ValueError("This command manages issues. Use GitHub to change PR assignments or merge/close a PR.")
        return issue

    async def confirm(self, interaction, issue):
        thread = await self.publish(issue)
        await interaction.followup.send(f"Updated {issue['html_url']}\nDiscussion: {thread.mention}", ephemeral=True)

    @issue_group.command(name="create", description="Create a GitHub issue and its Discord discussion.")
    async def create(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 256],
                     description: app_commands.Range[str, 1, 4000],
                     priority: str = "medium", assignee: discord.Member = None):
        if priority not in PRIORITIES:
            raise ValueError("Priority must be low, medium, high, or urgent.")
        data = {"title": title, "body": description}
        if assignee:
            link = store.get_link(interaction.guild_id, assignee.id)
            if not link:
                raise ValueError("The assignee must first use /issue link with their GitHub username.")
            data["assignees"] = [link["login"]]
        issue = await self.api.repo_request("POST", "issues", json=data)
        # Publish a base card before the optional priority operation so partial success is discoverable.
        await self.publish(issue)
        if assignee and link["login"].casefold() not in {user["login"].casefold() for user in issue.get("assignees", [])}:
            raise ValueError(f"Created {issue['html_url']}, but GitHub did not assign {link['login']}. Check their repository access.")
        issue = await self.api.set_priority(issue["number"], priority)
        await self.confirm(interaction, issue)

    @issue_group.command(name="assign", description="Assign an issue to a Discord member's linked GitHub account.")
    async def assign(self, interaction: discord.Interaction, number: app_commands.Range[int, 1],
                     member: discord.Member):
        await self.require_issue(number)
        link = store.get_link(interaction.guild_id, member.id)
        if not link:
            raise ValueError("That member must first use /issue link with their GitHub username.")
        issue = await self.api.repo_request("POST", f"issues/{number}/assignees", json={"assignees": [link["login"]]})
        if link["login"].casefold() not in {user["login"].casefold() for user in issue.get("assignees", [])}:
            raise ValueError("GitHub did not assign that user. Check that they can be assigned in this repository.")
        await self.confirm(interaction, issue)

    @issue_group.command(name="unassign", description="Remove a member's linked GitHub account from an issue.")
    async def unassign(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], member: discord.Member):
        await self.require_issue(number)
        link = store.get_link(interaction.guild_id, member.id)
        if not link:
            raise ValueError("That member has no linked GitHub username.")
        issue = await self.api.repo_request("DELETE", f"issues/{number}/assignees", json={"assignees": [link["login"]]})
        await self.confirm(interaction, issue)

    @issue_group.command(name="priority", description="Set low, medium, high, or urgent priority.")
    @app_commands.choices(level=[app_commands.Choice(name=value.title(), value=value) for value in PRIORITIES])
    async def priority(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], level: str):
        await self.require_issue(number)
        if level not in PRIORITIES:
            raise ValueError("Invalid priority.")
        await self.confirm(interaction, await self.api.set_priority(number, level))

    @issue_group.command(name="complete", description="Close a GitHub issue as completed and stop its reminders.")
    async def complete(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await self.require_issue(number)
        issue = await self.api.repo_request("PATCH", f"issues/{number}", json={"state": "closed", "state_reason": "completed"})
        store.delete_deadline(self.storage_repo, number)
        await self.confirm(interaction, issue)

    @issue_group.command(name="reopen", description="Reopen a completed issue; set a new deadline if needed.")
    async def reopen(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await self.require_issue(number)
        issue = await self.api.repo_request("PATCH", f"issues/{number}", json={"state": "open"})
        await self.confirm(interaction, issue)

    @issue_group.command(name="show", description="Show an issue or PR and open its Discord discussion.")
    async def show(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        issue = await self.api.issue(number)
        thread = await self.publish(issue)
        await interaction.followup.send(embed=issue_embed(issue, self.repo, self.storage_repo), content=thread.mention, ephemeral=True)

    @issue_group.command(name="list", description="List issues (25 per page; pull requests are excluded).")
    @app_commands.choices(state=[app_commands.Choice(name=value.title(), value=value) for value in ("open", "closed", "all")])
    async def list_issues(self, interaction: discord.Interaction, state: str = "open", page: app_commands.Range[int, 1] = 1):
        # GitHub includes PRs in its issue endpoint; use search for stable issue-only pages.
        query = f"repo:{self.repo} is:issue" + (f" is:{state}" if state != "all" else "")
        result = await self.api.request("GET", "/search/issues", params={"q": query, "per_page": 25, "page": page, "sort": "updated"})
        embed = discord.Embed(title=f"{self.repo} issues — page {page}", color=discord.Color.blurple())
        embed.description = ("\n".join(f"[#{issue['number']}]({issue['html_url']}) {discord.utils.escape_markdown(issue['title'])[:60]} — {issue['state']}"
                                       for issue in result["items"]) or "No issues on this page.")[:4000]
        embed.set_footer(text=f"{result['total_count']} matching issue(s); use the page option for more.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @issue_group.command(name="link", description="Set your GitHub username for assignments and reminder routing.")
    async def link(self, interaction: discord.Interaction, username: app_commands.Range[str, 1, 39]):
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", username):
            raise ValueError("Enter a GitHub username, without @ or a URL.")
        user = await self.api.request("GET", f"/users/{username}")
        store.save_link(interaction.guild_id, interaction.user.id, user["login"])
        await interaction.followup.send(f"Linked your reminder/assignment preference to **{user['login']}**. "
                                        "This does not sign you into GitHub; comments use the bot's GitHub token.", ephemeral=True)

    @issue_group.command(name="unlink", description="Remove your GitHub username preference.")
    async def unlink(self, interaction: discord.Interaction):
        store.delete_link(interaction.guild_id, interaction.user.id)
        await interaction.followup.send("Removed your GitHub username preference.", ephemeral=True)

    @issue_group.command(name="deadline", description="Set a deadline with an explicit timezone, or use clear to remove it.")
    async def deadline(self, interaction: discord.Interaction, number: app_commands.Range[int, 1],
                       due: str, recipient: discord.Member = None):
        issue = await self.require_issue(number)
        if due.casefold() == "clear":
            store.delete_deadline(self.storage_repo, number)
        else:
            if issue["state"] != "open":
                raise ValueError("Reopen the issue before setting a deadline.")
            parsed = parse_deadline(due)
            member = recipient or interaction.user
            channel = await self.channel()
            if not channel.permissions_for(member).view_channel:
                raise ValueError("The reminder recipient needs access to the GitHub activity channel.")
            store.save_deadline(self.storage_repo, number, interaction.guild_id, member.id, parsed.isoformat())
        await self.confirm(interaction, issue)


class MultiIssueCog(commands.Cog):
    """One slash-command group and webhook listener, with an independent relay per server."""
    issue_group = app_commands.Group(name="issue", description="Manage this server's GitHub issues and deadlines.", guild_only=True)

    def __init__(self, bot, workers):
        self.bot = bot
        self.workers = {worker.settings.guild_id: worker for worker in workers}
        self.runner = None

    def worker_for(self, interaction):
        worker = self.workers.get(interaction.guild_id)
        if worker is None:
            raise ValueError("GitHub integration is not configured for this Discord server.")
        return worker

    async def cog_load(self):
        store.init_github_db()
        first = next(iter(self.workers.values()))
        priority_locks = {}
        try:
            for worker in self.workers.values():
                # Preserve an existing deployment when its original activity channel is listed.
                legacy_channel = os.getenv("GITHUB_CHANNEL_ID", "")
                legacy_repo = os.getenv("GITHUB_REPO", "SirArthur2009/discord-bot")
                if legacy_channel == str(worker.settings.channel_id) and legacy_repo.casefold() == worker.repo.casefold():
                    store.migrate_repo_scope(legacy_repo, worker.storage_repo)
                await worker.cog_load(serve_http=False)
                worker.api.priority_lock = priority_locks.setdefault(worker.repo.casefold(), asyncio.Lock())
            app = web.Application(client_max_size=26 * 1024 * 1024)
            app.router.add_post("/github/webhook", self.webhook)
            app.router.add_get("/health", first.health)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            await web.TCPSite(self.runner, "0.0.0.0", first.settings.port).start()
        except Exception:
            await self.cog_unload()
            raise

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()
        for worker in self.workers.values():
            await worker.cog_unload()

    async def webhook(self, request):
        body = await request.read()
        first = next(iter(self.workers.values()))
        if not valid_signature(body, first.settings.secret, request.headers.get("X-Hub-Signature-256")):
            raise web.HTTPUnauthorized(text="Invalid signature")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(payload, dict) or not isinstance(payload.get("repository"), dict):
            raise web.HTTPBadRequest(text="Expected a repository object")
        repo = payload["repository"].get("full_name", "")
        if not isinstance(repo, str):
            raise web.HTTPBadRequest(text="Invalid repository")
        workers = [worker for worker in self.workers.values() if worker.repo.casefold() == repo.casefold()]
        if not workers:
            raise web.HTTPForbidden(text="Unexpected repository")
        event = request.headers.get("X-GitHub-Event", "")
        delivery = request.headers.get("X-GitHub-Delivery", "")
        if not delivery or len(delivery) > 200:
            raise web.HTTPBadRequest(text="Missing or invalid delivery ID")
        if event in {"issues", "pull_request", "issue_comment", "push", "pull_request_review", "pull_request_review_comment"}:
            for worker in workers:
                store.enqueue(f"github:{worker.settings.channel_id}:{delivery}", event, payload, worker.storage_repo)
        return web.Response(status=202)

    async def interaction_check(self, interaction):
        return await self.worker_for(interaction).interaction_check(interaction)

    async def cog_app_command_error(self, interaction, error):
        await IssueCog.cog_app_command_error(self, interaction, error)

    @commands.Cog.listener()
    async def on_message(self, message):
        for worker in self.workers.values():
            await worker.on_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, event):
        for worker in self.workers.values():
            await worker.on_raw_message_edit(event)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, event):
        for worker in self.workers.values():
            await worker.on_raw_message_delete(event)

    @issue_group.command(name="create", description="Create a GitHub issue and its Discord discussion.")
    @app_commands.choices(priority=[app_commands.Choice(name=value.title(), value=value) for value in PRIORITIES])
    async def create(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 256],
                     description: app_commands.Range[str, 1, 4000], priority: str = "medium", assignee: discord.Member = None):
        await IssueCog.create.callback(self.worker_for(interaction), interaction, title, description, priority, assignee)

    @issue_group.command(name="assign", description="Assign an issue to a member's linked GitHub account.")
    async def assign(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], member: discord.Member):
        await IssueCog.assign.callback(self.worker_for(interaction), interaction, number, member)

    @issue_group.command(name="unassign", description="Remove a member's linked GitHub account from an issue.")
    async def unassign(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], member: discord.Member):
        await IssueCog.unassign.callback(self.worker_for(interaction), interaction, number, member)

    @issue_group.command(name="priority", description="Set low, medium, high, or urgent priority.")
    @app_commands.choices(level=[app_commands.Choice(name=value.title(), value=value) for value in PRIORITIES])
    async def priority(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], level: str):
        await IssueCog.priority.callback(self.worker_for(interaction), interaction, number, level)

    @issue_group.command(name="complete", description="Complete a GitHub issue and stop reminders.")
    async def complete(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await IssueCog.complete.callback(self.worker_for(interaction), interaction, number)

    @issue_group.command(name="reopen", description="Reopen a GitHub issue.")
    async def reopen(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await IssueCog.reopen.callback(self.worker_for(interaction), interaction, number)

    @issue_group.command(name="show", description="Show an issue or PR and its Discord discussion.")
    async def show(self, interaction: discord.Interaction, number: app_commands.Range[int, 1]):
        await IssueCog.show.callback(self.worker_for(interaction), interaction, number)

    @issue_group.command(name="list", description="List this server's GitHub issues, 25 per page.")
    @app_commands.choices(state=[app_commands.Choice(name=value.title(), value=value) for value in ("open", "closed", "all")])
    async def list_issues(self, interaction: discord.Interaction, state: str = "open", page: app_commands.Range[int, 1] = 1):
        await IssueCog.list_issues.callback(self.worker_for(interaction), interaction, state, page)

    @issue_group.command(name="link", description="Set your GitHub username in this server.")
    async def link(self, interaction: discord.Interaction, username: app_commands.Range[str, 1, 39]):
        await IssueCog.link.callback(self.worker_for(interaction), interaction, username)

    @issue_group.command(name="unlink", description="Remove your GitHub username preference in this server.")
    async def unlink(self, interaction: discord.Interaction):
        await IssueCog.unlink.callback(self.worker_for(interaction), interaction)

    @issue_group.command(name="deadline", description="Set this server's deadline, or use clear to remove it.")
    async def deadline(self, interaction: discord.Interaction, number: app_commands.Range[int, 1], due: str,
                       recipient: discord.Member = None):
        await IssueCog.deadline.callback(self.worker_for(interaction), interaction, number, due, recipient)


def github_settings_for_servers(servers):
    enabled = [server for server in servers if server.github_channel_id is not None]
    if not enabled:
        return []
    token = os.getenv("GITHUB_TOKEN", "")
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not token or not secret:
        raise RuntimeError("Configured GitHub activity channels require GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET.")
    try:
        port = int(os.getenv("PORT", "8080"))
    except ValueError as error:
        raise RuntimeError("PORT must be a numeric TCP port.") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535.")
    return [(server, GitHubSettings(token, server.github_repo, server.github_channel_id, secret, port,
                                    server.guild_id, f"{server.github_repo}#channel:{server.github_channel_id}"))
            for server in enabled]


async def install_github(bot, role_id, servers=()):
    if servers:
        configured = github_settings_for_servers(servers)
        if configured:
            workers = [IssueCog(bot, settings, server.command_role_id) for server, settings in configured]
            await bot.add_cog(MultiIssueCog(bot, workers))
        return
    settings = GitHubSettings.from_environment()
    if settings:
        await bot.add_cog(IssueCog(bot, settings, role_id))
