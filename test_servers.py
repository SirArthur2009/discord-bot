import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from discord.ext import commands

import github_store as store
from github_integration import GitHubSettings, IssueCog, MultiIssueCog, github_settings_for_servers, issue_embed
from server_config import ServerConfig, command_role_for_guild, load_servers, sync_commands
from test_github import DatabaseFixture, sample_issue


class ConfigurationTests(unittest.TestCase):
    def load(self, items):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            path.write_text(json.dumps(items), encoding="utf-8")
            with patch.dict(os.environ, {"SERVER_CONFIG_PATH": str(path)}):
                return load_servers()

    def test_two_servers_can_share_repo_with_separate_roles(self):
        servers = self.load([{"guild_id": "1", "command_role_id": "11", "github_channel_id": "101"},
                             {"guild_id": "2", "command_role_id": "22", "github_channel_id": "202"}])
        self.assertEqual(servers[0].github_repo, servers[1].github_repo)
        self.assertEqual(command_role_for_guild(1, servers, 999), 11)
        self.assertEqual(command_role_for_guild(2, servers, 999), 22)
        self.assertIsNone(command_role_for_guild(3, servers, 999))

    def test_claim_only_server_needs_no_github_credentials(self):
        servers = self.load([{"guild_id": 1, "command_role_id": 11}])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(github_settings_for_servers(servers), [])

    def test_invalid_duplicate_and_missing_server_ids_fail(self):
        base = {"guild_id": 1, "command_role_id": 11, "github_channel_id": 101}
        for items in ([], {}, [base, base], [{**base, "guild_id": True}],
                      [{"guild_id": 1}], [{**base, "github_repo": "invalid"}],
                      [base, {**base, "guild_id": 2}]):
            with self.subTest(items=items), self.assertRaises(RuntimeError):
                self.load(items)

    def test_multi_config_uses_common_credentials_and_channel_scopes(self):
        servers = [ServerConfig(1, 11, 101, "owner/first"), ServerConfig(2, 22, 202, "owner/second")]
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test", "GITHUB_WEBHOOK_SECRET": "secret", "PORT": "8080"}, clear=True):
            configured = github_settings_for_servers(servers)
        self.assertEqual(configured[1][1].repo, "owner/second")
        self.assertEqual(configured[1][1].guild_id, 2)
        self.assertNotEqual(configured[0][1].scope, configured[1][1].scope)

    def test_legacy_role_fallback_still_works(self):
        self.assertEqual(command_role_for_guild(123, [], 11), 11)


class CommandSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_sync_to_both_configured_servers(self):
        tree = SimpleNamespace(copy_global_to=MagicMock(), remove_command=MagicMock(), sync=AsyncMock())
        await sync_commands(tree, [ServerConfig(1, 11), ServerConfig(2, 22)], "999")
        self.assertEqual([call.kwargs["guild"].id for call in tree.sync.call_args_list], [1, 2])

    async def test_claim_only_server_does_not_get_issue_commands(self):
        tree = SimpleNamespace(copy_global_to=MagicMock(), remove_command=MagicMock(), sync=AsyncMock())
        await sync_commands(tree, [ServerConfig(1, 11, 101), ServerConfig(2, 22)], None)
        tree.remove_command.assert_called_once()
        self.assertEqual(tree.remove_command.call_args.args, ("issue",))
        self.assertEqual(tree.remove_command.call_args.kwargs["guild"].id, 2)

    async def test_legacy_global_sync_is_preserved(self):
        tree = SimpleNamespace(copy_global_to=MagicMock(), sync=AsyncMock())
        await sync_commands(tree, [], None)
        tree.sync.assert_awaited_once_with()


class MultiServerTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.workers = []
        for guild, role, channel in [(1, 11, 101), (2, 22, 202)]:
            settings = GitHubSettings("test", "owner/repo", channel, "secret", 0, guild, f"owner/repo#channel:{channel}")
            worker = IssueCog(self.bot, settings, role)
            worker.api = SimpleNamespace(issue=AsyncMock(return_value=sample_issue()), repo_request=AsyncMock())
            self.workers.append(worker)
        self.cog = MultiIssueCog(self.bot, self.workers)

    async def make_webhook_client(self):
        app = web.Application()
        app.router.add_post("/github/webhook", self.cog.webhook)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def post(self, client, repo, signature=True):
        body = json.dumps({"repository": {"full_name": repo}, "issue": sample_issue(), "action": "opened"}).encode()
        headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "delivery"}
        if signature:
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        return await client.post("/github/webhook", data=body, headers=headers)

    async def test_shared_repo_webhook_is_queued_once_for_each_server(self):
        client = await self.make_webhook_client()
        self.assertEqual((await self.post(client, "owner/repo", False)).status, 401)
        self.assertEqual((await self.post(client, "owner/repo")).status, 202)
        self.assertEqual((await self.post(client, "owner/repo")).status, 202)
        for worker in self.workers:
            self.assertIsNotNone(store.next_job(worker.storage_repo))
        self.assertEqual(len(store.execute("SELECT * FROM github_jobs", rows=True)), 2)

    async def test_different_repo_webhook_only_reaches_matching_server(self):
        self.workers[1].repo = "owner/other"
        client = await self.make_webhook_client()
        await self.post(client, "owner/repo")
        self.assertIsNotNone(store.next_job(self.workers[0].storage_repo))
        self.assertIsNone(store.next_job(self.workers[1].storage_repo))
        self.assertEqual((await self.post(client, "outside/repo")).status, 403)

    async def test_thread_comment_and_deadline_records_are_separate_for_same_issue(self):
        for index, worker in enumerate(self.workers):
            scope = worker.storage_repo
            store.save_thread(scope, 7, 1000 + index, 2000 + index)
            store.save_comment(scope, 99, 3000 + index, "github")
            store.save_deadline(scope, 7, index + 1, 42, f"2030-01-0{index + 1}T00:00:00+00:00")
        for index, worker in enumerate(self.workers):
            scope = worker.storage_repo
            self.assertEqual(store.get_thread(scope, 7)["thread_id"], 2000 + index)
            self.assertEqual(store.get_comment(scope, 99)["message_id"], 3000 + index)
            embed = issue_embed(sample_issue(), worker.repo, scope)
            self.assertIn("Deadline", [field.name for field in embed.fields])
            self.assertEqual(store.get_deadline(scope, 7)["guild_id"], index + 1)
        store.delete_deadline(self.workers[0].storage_repo, 7)
        self.assertIsNotNone(store.get_deadline(self.workers[1].storage_repo, 7))

    async def test_discord_origin_comment_only_suppresses_echo_in_its_original_server(self):
        origin, other = self.workers
        store.save_comment(origin.storage_repo, 99, 42, "discord")
        comment = {"id": 99, "body": "Arthur's reply", "user": {"login": "Arthur"}, "html_url": "https://github.com/comment"}
        other.api.repo_request.return_value = comment
        first_thread = SimpleNamespace(send=AsyncMock())
        second_thread = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=43)))
        payload = {"comment": comment, "action": "created"}
        await origin.relay_github_comment(first_thread, payload)
        await other.relay_github_comment(second_thread, payload)
        first_thread.send.assert_not_awaited()
        second_thread.send.assert_awaited_once()
        self.assertEqual(store.get_comment(other.storage_repo, 99)["source"], "github")

    async def test_commands_route_to_server_worker(self):
        for worker in self.workers:
            worker.publish = AsyncMock(return_value=SimpleNamespace(mention="thread"))
        interaction = SimpleNamespace(guild_id=2, followup=SimpleNamespace(send=AsyncMock()))
        await self.cog.show.callback(self.cog, interaction, 7)
        self.workers[0].api.issue.assert_not_awaited()
        self.workers[1].api.issue.assert_awaited_once_with(7)
        with self.assertRaises(ValueError):
            self.cog.worker_for(SimpleNamespace(guild_id=3))

    async def test_member_needs_the_role_in_current_server(self):
        user = MagicMock(spec=discord.Member)
        user.roles = [SimpleNamespace(id=11)]
        interaction = SimpleNamespace(user=user, guild_id=2, response=SimpleNamespace(defer=AsyncMock()))
        from discord import app_commands
        with self.assertRaises(app_commands.CheckFailure):
            await self.cog.interaction_check(interaction)
        interaction.response.defer.assert_not_awaited()

    async def test_configured_channel_must_belong_to_configured_server(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild.id = 2
        self.bot.get_channel.return_value = channel
        with self.assertRaises(ValueError):
            await self.workers[0].channel()

    async def test_multi_group_registers_once_and_preserves_all_commands(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        self.addAsyncCleanup(bot.close)
        cog = MultiIssueCog(bot, self.workers)
        with patch.object(cog, "cog_load", new=AsyncMock()):
            await bot.add_cog(cog)
        group = bot.tree.get_command("issue")
        self.assertEqual(len(group.commands), 11)
        self.assertIs(group.get_command("show").binding, cog)
        await bot.remove_cog("MultiIssueCog")

    async def test_shared_web_server_starts_and_closes_worker_sessions(self):
        ready = asyncio.Event()
        self.bot.wait_until_ready = ready.wait
        with patch.dict(os.environ, {"GITHUB_CHANNEL_ID": ""}):
            await self.cog.cog_load()
        try:
            self.assertIs(self.workers[0].api.priority_lock, self.workers[1].api.priority_lock)
            self.assertFalse(self.workers[0].api.session.closed)
        finally:
            await self.cog.cog_unload()
        self.assertTrue(all(worker.api.session.closed for worker in self.workers))

    async def test_legacy_state_migration_preserves_pending_jobs_and_links(self):
        store.save_thread("owner/repo", 7, 10, 11)
        store.save_link(1, 42, "Arthur")
        store.enqueue("legacy", "issues", {}, "owner/repo")
        scope = self.workers[0].storage_repo
        store.migrate_repo_scope("owner/repo", scope)
        self.assertEqual(store.get_thread(scope, 7)["thread_id"], 11)
        self.assertIsNotNone(store.next_job(scope))
        self.assertEqual(store.get_link(1, 42)["login"], "Arthur")


if __name__ == "__main__":
    unittest.main()
