import hashlib
import hmac
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import database
import github_store as store
from github_api import GitHubError, GitHubAPI
from github_integration import (GitHubSettings, IssueCog, parse_deadline,
                                reminder_stage, valid_signature, issue_embed)


def sample_issue(number=7, state="open"):
    return {"number": number, "title": "A test issue", "html_url": f"https://github.com/owner/repo/issues/{number}",
            "body": "Description", "state": state, "labels": [], "assignees": [{"login": "Arthur"}]}


class DatabaseFixture:
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = patch.object(database, "DB_PATH", str(Path(directory.name) / "github.db"))
        override.start()
        self.addCleanup(override.stop)
        database.init_db()
        store.init_github_db()


class StoreTests(DatabaseFixture, unittest.TestCase):
    def test_duplicate_delivery_is_only_queued_once_and_survives_restart(self):
        store.enqueue("delivery", "issues", {"number": 1})
        store.enqueue("delivery", "issues", {"number": 2})
        store.init_github_db()
        job = store.next_job()
        self.assertEqual(json.loads(job["payload"]), {"number": 1})
        store.finish_job(job["id"])
        store.enqueue("delivery", "issues", {})
        self.assertIsNone(store.next_job())

    def test_failed_jobs_wait_before_retrying(self):
        store.enqueue("delivery", "issues", {})
        job = store.next_job()
        store.retry_job(job, 30)
        self.assertIsNone(store.next_job())
        row = store.execute("SELECT attempts FROM github_jobs WHERE id = ?", (job["id"],), one=True)
        self.assertEqual(row["attempts"], 1)

    def test_jobs_for_another_repo_are_not_processed(self):
        store.enqueue("delivery", "issues", {}, "other/repo")
        self.assertIsNone(store.next_job("owner/repo"))
        self.assertIsNotNone(store.next_job("other/repo"))

    def test_deadline_changes_reset_notifications_and_completion_removes_due_date(self):
        store.save_deadline("owner/repo", 7, 1, 42, "2030-01-01T00:00:00+00:00")
        deadline = store.get_deadline("owner/repo", 7)
        store.mark_reminder(deadline, 42, "24h")
        self.assertTrue(store.reminder_sent(deadline, 42, "24h"))
        store.save_deadline("owner/repo", 7, 1, 42, "2030-02-01T00:00:00+00:00")
        self.assertFalse(store.reminder_sent(store.get_deadline("owner/repo", 7), 42, "24h"))
        store.delete_deadline("owner/repo", 7)
        self.assertIsNone(store.get_deadline("owner/repo", 7))

    def test_link_routing_is_case_insensitive_and_scoped_to_guild(self):
        store.save_link(1, 42, "Arthur")
        store.save_link(2, 43, "Arthur")
        self.assertEqual(store.linked_assignees(1, ["arthur"]), {42})
        store.delete_link(1, 42)
        self.assertEqual(store.linked_assignees(1, ["Arthur"]), set())

    def test_issue_card_includes_local_deadline_and_stays_within_limits(self):
        store.save_deadline("owner/repo", 7, 1, 42, "2030-01-01T00:00:00+00:00")
        issue = sample_issue()
        issue.update(title="x" * 1000, body="x" * 10000)
        embed = issue_embed(issue, "owner/repo")
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertIn("Deadline", [field.name for field in embed.fields])


class ValidationTests(unittest.TestCase):
    def test_github_signature_known_vector_and_tampering(self):
        signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        self.assertTrue(valid_signature(b"Hello, World!", "It's a Secret to Everybody", signature))
        self.assertFalse(valid_signature(b"changed", "It's a Secret to Everybody", signature))
        self.assertFalse(valid_signature(b"Hello, World!", "secret", None))

    def test_deadlines_require_offset_future_time_and_convert_to_utc(self):
        self.assertEqual(parse_deadline("2030-01-01T12:00:00-07:00").hour, 19)
        for value in ("2030-01-01T12:00:00", "yesterday", "2000-01-01T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_deadline(value)

    def test_reminder_stage_boundaries(self):
        now = datetime.now(timezone.utc)
        for hours, expected in [(25, None), (24, "24h"), (2, "24h"), (1, "1h"), (0, "overdue")]:
            self.assertEqual(reminder_stage(now + timedelta(hours=hours), now), expected)

    def test_configuration_is_optional_but_partial_configuration_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(GitHubSettings.from_environment())
            os.environ["GITHUB_TOKEN"] = "test"
            with self.assertRaises(RuntimeError):
                GitHubSettings.from_environment()
            os.environ.update(GITHUB_CHANNEL_ID="123", GITHUB_WEBHOOK_SECRET="secret")
            self.assertEqual(GitHubSettings.from_environment().repo, "SirArthur2009/discord-bot")


class IntegrationTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.cog = IssueCog(self.bot, GitHubSettings("test", "owner/repo", 100, "secret"), 1)
        self.cog.api = SimpleNamespace(issue=AsyncMock(return_value=sample_issue()),
                                       repo_request=AsyncMock(), request=AsyncMock())

    async def test_http_webhook_rejects_forgery_and_wrong_repo_and_deduplicates(self):
        app = web.Application()
        app.router.add_post("/github/webhook", self.cog.webhook)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)

        async def send(repo, signature=True):
            body = json.dumps({"repository": {"full_name": repo}, "issue": sample_issue()}).encode()
            headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "delivery"}
            if signature:
                headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            return await client.post("/github/webhook", data=body, headers=headers)

        self.assertEqual((await send("owner/repo", False)).status, 401)
        self.assertEqual((await send("other/repo")).status, 403)
        self.assertEqual((await send("owner/repo")).status, 202)
        self.assertEqual((await send("owner/repo")).status, 202)
        self.assertEqual(len(store.execute("SELECT * FROM github_jobs", rows=True)), 1)

    async def test_discord_comment_is_posted_once_and_echo_is_ignored(self):
        async def empty_comments(number):
            if False:
                yield None
        self.cog.api.comments = empty_comments
        self.cog.api.repo_request.return_value = {"id": 99}
        payload = {"number": 7, "message_id": 42, "thread_id": 8, "guild_id": 1,
                   "user_id": 3, "author": "Arthur", "content": "Please fix this.", "attachments": []}
        await self.cog.forward_discord_comment(payload)
        await self.cog.forward_discord_comment(payload)
        self.cog.api.repo_request.assert_awaited_once()
        body = self.cog.api.repo_request.call_args.kwargs["json"]["body"]
        self.assertIn("Arthur on Discord", body)
        self.assertTrue(body.endswith("<!-- claimbot-discord:42 -->"))
        thread = SimpleNamespace(send=AsyncMock())
        await self.cog.relay_github_comment(thread, {"comment": {"id": 99}, "action": "created"})
        thread.send.assert_not_awaited()

    async def test_retry_recovers_post_accepted_before_database_save(self):
        async def existing_comments(number):
            yield {"id": 99, "body": "Already posted\n<!-- claimbot-discord:42 -->"}
        self.cog.api.comments = existing_comments
        await self.cog.forward_discord_comment({"number": 7, "message_id": 42})
        self.cog.api.repo_request.assert_not_awaited()
        self.assertEqual(store.get_comment("owner/repo", 99)["source"], "discord")

    async def test_github_comment_creates_updates_and_deletes_discord_copy(self):
        comment = {"id": 99, "body": "First", "user": {"login": "Arthur"}, "html_url": "https://github.com/comment"}
        self.cog.api.repo_request.return_value = comment
        partial = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        thread = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=42)),
                                 get_partial_message=MagicMock(return_value=partial))
        await self.cog.relay_github_comment(thread, {"comment": comment, "action": "created"})
        comment["body"] = "Updated"
        await self.cog.relay_github_comment(thread, {"comment": comment, "action": "edited"})
        await self.cog.relay_github_comment(thread, {"comment": comment, "action": "deleted"})
        thread.send.assert_awaited_once()
        self.assertIn("Updated", partial.edit.call_args.kwargs["content"])
        partial.delete.assert_awaited_once()

    async def test_pending_creation_keeps_edit_and_delete_jobs_for_retry(self):
        store.enqueue("discord:42", "discord_comment", {}, "owner/repo")
        for event in ("discord_comment_edit", "discord_comment_delete"):
            with self.assertRaises(ValueError):
                await self.cog.update_discord_comment(event, {"message_id": 42})

    async def test_closed_issue_cancels_deadlines_without_dm(self):
        store.save_deadline("owner/repo", 7, 1, 42, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.cog.api.issue.return_value = sample_issue(state="closed")
        await self.cog.send_reminders(store.get_deadline("owner/repo", 7))
        self.assertIsNone(store.get_deadline("owner/repo", 7))
        self.bot.fetch_user.assert_not_called()

    async def test_reminders_go_to_recipient_and_linked_assignee_only_once(self):
        store.save_deadline("owner/repo", 7, 1, 42, (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
        store.save_link(1, 43, "Arthur")
        members = {uid: SimpleNamespace(send=AsyncMock()) for uid in (42, 43)}
        guild = SimpleNamespace(id=1, get_member=lambda uid: members.get(uid), fetch_member=AsyncMock())
        channel = SimpleNamespace(guild=guild, permissions_for=lambda member: SimpleNamespace(view_channel=True))
        self.cog.channel = AsyncMock(return_value=channel)
        deadline = store.get_deadline("owner/repo", 7)
        await self.cog.send_reminders(deadline)
        await self.cog.send_reminders(deadline)
        for member in members.values():
            member.send.assert_awaited_once()

    async def test_completion_closes_github_issue_and_clears_deadline(self):
        store.save_deadline("owner/repo", 7, 1, 42, "2030-01-01T00:00:00+00:00")
        self.cog.api.repo_request.return_value = sample_issue(state="closed")
        self.cog.confirm = AsyncMock()
        interaction = SimpleNamespace()
        await self.cog.complete.callback(self.cog, interaction, 7)
        self.cog.api.repo_request.assert_awaited_once_with("PATCH", "issues/7", json={"state": "closed", "state_reason": "completed"})
        self.assertIsNone(store.get_deadline("owner/repo", 7))

    async def test_pr_cannot_be_completed_through_issue_command(self):
        issue = sample_issue()
        issue["pull_request"] = {}
        self.cog.api.issue.return_value = issue
        with self.assertRaises(ValueError):
            await self.cog.complete.callback(self.cog, SimpleNamespace(), 7)
        self.cog.api.repo_request.assert_not_awaited()

    async def test_direct_reply_to_activity_card_is_queued_but_unrelated_message_is_not(self):
        store.save_thread("owner/repo", 7, 10, 11)
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.id = 42
        member.display_name = "Arthur"
        member.roles = [SimpleNamespace(id=1)]
        message = SimpleNamespace(author=member, webhook_id=None, channel=SimpleNamespace(id=100),
                                  reference=SimpleNamespace(message_id=10), content="Reply", attachments=[],
                                  id=12, guild=SimpleNamespace(id=1))
        await self.cog.on_message(message)
        job = store.next_job("owner/repo")
        self.assertEqual(json.loads(job["payload"])["number"], 7)
        store.finish_job(job["id"])
        message.id = 13
        message.reference = None
        await self.cog.on_message(message)
        self.assertIsNone(store.next_job("owner/repo"))
        message.reference = SimpleNamespace(message_id=10)
        member.roles = []
        await self.cog.on_message(message)
        self.assertIsNone(store.next_job("owner/repo"))

    async def test_command_access_requires_role_and_correct_server(self):
        user = MagicMock(spec=discord.Member)
        user.roles = []
        interaction = SimpleNamespace(user=user, guild_id=1,
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        with self.assertRaises(app_commands.CheckFailure):
            await self.cog.interaction_check(interaction)
        interaction.response.defer.assert_not_awaited()
        user.roles = [SimpleNamespace(id=1)]
        self.cog.channel = AsyncMock(return_value=SimpleNamespace(guild=SimpleNamespace(id=2)))
        self.assertFalse(await self.cog.interaction_check(interaction))
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.cog.channel.return_value.guild.id = 1
        self.assertTrue(await self.cog.interaction_check(interaction))

    async def test_edit_reads_latest_discord_body_and_preserves_attribution(self):
        store.save_comment("owner/repo", 99, 42, "discord")
        original = "**Arthur on Discord** (user ID 3)\n\nOld\n\n[Original Discord message](https://discord.com/example)\n\n<!-- claimbot-discord:42 -->"
        self.cog.api.repo_request.return_value = {"body": original}
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.roles = [SimpleNamespace(id=1)]
        channel = SimpleNamespace(id=100, fetch_message=AsyncMock(return_value=SimpleNamespace(content="Newest", attachments=[], author=member)))
        self.bot.get_channel.return_value = channel
        await self.cog.update_discord_comment("discord_comment_edit", {"message_id": 42, "thread_id": 11, "content": "Stale", "attachments": []})
        method, path = self.cog.api.repo_request.call_args.args
        self.assertEqual((method, path), ("PATCH", "issues/comments/99"))
        body = self.cog.api.repo_request.call_args.kwargs["json"]["body"]
        self.assertIn("Newest", body)
        self.assertNotIn("Stale", body)
        self.assertTrue(body.endswith("<!-- claimbot-discord:42 -->"))

    async def test_raw_edit_is_persisted_before_network_requests(self):
        store.save_comment("owner/repo", 99, 42, "discord")
        event = SimpleNamespace(message_id=42, channel_id=100, data={"content": "updated", "edited_timestamp": "revision"})
        await self.cog.on_raw_message_edit(event)
        job = store.next_job("owner/repo")
        self.assertEqual(job["event"], "discord_comment_edit")
        self.bot.fetch_channel.assert_not_called()

    async def test_discord_deletion_only_deletes_bot_forwarded_comments(self):
        store.save_comment("owner/repo", 99, 42, "discord")
        await self.cog.update_discord_comment("discord_comment_delete", {"message_id": 42})
        self.cog.api.repo_request.assert_awaited_once_with("DELETE", "issues/comments/99")
        self.cog.api.repo_request.reset_mock()
        store.save_comment("owner/repo", 100, 43, "github")
        await self.cog.update_discord_comment("discord_comment_delete", {"message_id": 43})
        self.cog.api.repo_request.assert_not_awaited()

    async def test_issue_group_registers_with_cog_checks(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        self.addAsyncCleanup(bot.close)
        cog = IssueCog(bot, self.cog.settings, 1)
        with patch.object(cog, "cog_load", new=AsyncMock()):
            await bot.add_cog(cog)
        group = bot.tree.get_command("issue")
        self.assertIsNotNone(group)
        self.assertEqual({command.name for command in group.commands},
                         {"create", "assign", "unassign", "priority", "complete", "reopen",
                          "show", "list", "link", "unlink", "deadline"})
        self.assertIs(group.get_command("create").binding, cog)
        await bot.remove_cog("IssueCog")

    async def test_thread_mapping_survives_creation_failure_without_duplicate_card(self):
        channel = SimpleNamespace(send=AsyncMock(), fetch_message=AsyncMock())
        thread = SimpleNamespace(id=10, archived=False)
        message = SimpleNamespace(id=10, create_thread=AsyncMock(side_effect=[RuntimeError("temporary"), thread]), edit=AsyncMock())
        channel.send.return_value = message
        channel.fetch_message.return_value = message
        self.cog.channel = AsyncMock(return_value=channel)
        self.bot.get_channel.return_value = None
        self.bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="missing"), "missing"))
        with self.assertRaises(RuntimeError):
            await self.cog.publish(sample_issue())
        await self.cog.publish(sample_issue())
        channel.send.assert_awaited_once()
        self.assertEqual(store.get_thread("owner/repo", 7)["thread_id"], 10)


class PriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_preserves_other_labels(self):
        api = GitHubAPI("test", "owner/repo")
        self.addAsyncCleanup(api.close)
        api.repo_request = AsyncMock(side_effect=[{}, {}, [{"name": "bug"}, {"name": "priority:low"}, {"name": "priority:high"}], None, sample_issue()])
        await api.set_priority(7, "high")
        calls = api.repo_request.call_args_list
        self.assertEqual(calls[1].kwargs["json"], {"labels": ["priority:high"]})
        self.assertEqual(calls[3].args, ("DELETE", "issues/7/labels/priority%3Alow"))
        self.assertFalse(any(call.args[0] == "PATCH" for call in calls))


if __name__ == "__main__":
    unittest.main()
