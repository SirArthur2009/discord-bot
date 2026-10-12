import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

import github_store as store
from github_auth import GitHubAuth
from github_api import GitHubError
from github_integration import GitHubSettings, IssueCog
from test_github import DatabaseFixture, sample_issue


class AuthTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.worker = IssueCog(self.bot, GitHubSettings("owner", "owner/repo", 100, "secret", guild_id=1), 1)
        self.worker.api = SimpleNamespace(request=AsyncMock(return_value={"id": 9}), repo_request=AsyncMock(return_value={"id": 99}))
        async def comments(number):
            if False:
                yield None
        self.worker.api.comments = comments
        self.auth = GitHubAuth(self.bot, [self.worker], "client", Fernet.generate_key().decode())
        self.auth.oauth = AsyncMock()
        self.auth.notify = AsyncMock()
        self.worker.auth = self.auth

    def save_auth(self, user_id=3, expired=False):
        data = {"access_token": "user-secret", "refresh_token": "refresh-secret", "repo": "owner/repo",
                "expires_at": time.time() + (-1 if expired else 1000), "refresh_expires_at": time.time() + 10000}
        store.execute("INSERT OR REPLACE INTO github_auth VALUES (?, ?, ?, ?)", (1, user_id, "Alice", self.auth.encrypt(data)))
        return data

    def payload(self, message_id=42):
        return {"number": 7, "message_id": message_id, "thread_id": 8, "guild_id": 1,
                "user_id": 3, "author": "Alice", "content": "Please fix this.", "attachments": []}

    async def test_credentials_are_encrypted_and_scoped_to_discord_user_and_guild(self):
        data = self.save_auth()
        row = store.execute("SELECT * FROM github_auth", one=True)
        self.assertNotIn("secret", row["credentials"])
        self.assertEqual((await self.auth.token_for(1, 3))["access_token"], data["access_token"])
        self.assertIsNone(await self.auth.token_for(2, 3))
        self.assertIsNone(await self.auth.token_for(1, 4))

    async def test_token_refresh_rotates_credentials_and_revocation_disconnects(self):
        self.save_auth(expired=True)
        self.auth.oauth.return_value = {"access_token": "new-secret", "refresh_token": "new-refresh",
                                       "expires_in": 28800, "refresh_token_expires_in": 15897600}
        self.assertEqual((await self.auth.token_for(1, 3))["access_token"], "new-secret")
        self.assertEqual((await self.auth.token_for(1, 3))["access_token"], "new-secret")
        self.auth.oauth.assert_awaited_once()
        self.save_auth(expired=True)
        self.auth.oauth.return_value = {"error": "bad_refresh_token"}
        self.assertIsNone(await self.auth.token_for(1, 3))
        self.assertIsNone(store.execute("SELECT * FROM github_auth", one=True))

    async def test_every_unlinked_post_uses_owner_and_gets_one_dm_even_with_retries(self):
        await self.worker.forward_discord_comment(self.payload())
        await self.worker.forward_discord_comment(self.payload())
        await self.worker.forward_discord_comment(self.payload(43))
        self.assertEqual(self.worker.api.repo_request.await_count, 2)
        self.assertEqual(self.auth.notify.await_count, 2)
        self.assertIn("SirArthur2009", self.auth.notify.call_args.args[1])
        self.assertEqual(store.get_comment_by_message("owner/repo", 43)["actor_user_id"], 0)

    async def test_signed_in_comment_uses_user_token_and_does_not_prompt(self):
        self.save_auth()
        api = SimpleNamespace(request=AsyncMock(), repo_request=AsyncMock(return_value={"id": 88}), close=AsyncMock())
        with patch("github_auth.GitHubAPI", return_value=api) as factory:
            await self.worker.forward_discord_comment(self.payload())
        factory.assert_called_once_with("user-secret", "owner/repo")
        api.repo_request.assert_awaited_once()
        self.worker.api.repo_request.assert_not_awaited()
        self.auth.notify.assert_not_awaited()
        self.assertEqual(store.get_comment("owner/repo", 88)["actor_user_id"], 3)
        api.close.assert_awaited_once()

    async def test_unauthenticated_username_link_does_not_impersonate_that_user(self):
        store.save_link(1, 3, "Alice")
        await self.worker.forward_discord_comment(self.payload())
        self.worker.api.repo_request.assert_awaited_once()
        self.auth.notify.assert_awaited_once()

    async def test_revoked_token_and_missing_repo_access_fall_back_but_rate_limits_retry(self):
        for status in (401, 403, 404, 429):
            self.save_auth()
            api = SimpleNamespace(request=AsyncMock(side_effect=GitHubError(status, "failed")), close=AsyncMock())
            with patch("github_auth.GitHubAPI", return_value=api):
                if status == 429:
                    with self.assertRaises(GitHubError):
                        async with self.auth.posting_api(1, 3, "owner/repo"):
                            pass
                else:
                    async with self.auth.posting_api(1, 3, "owner/repo") as selected:
                        self.assertIsNone(selected)
            api.close.assert_awaited_once()

    async def test_crash_recovery_preserves_user_author_for_delete(self):
        store.execute("INSERT INTO github_comment_actors VALUES (?, ?, ?, ?)", ("owner/repo", 42, 1, 3))
        async def comments(number):
            yield {"id": 88, "body": "existing\n<!-- claimbot-discord:42 -->"}
        self.worker.api.comments = comments
        await self.worker.forward_discord_comment(self.payload())
        self.save_auth()
        api = SimpleNamespace(request=AsyncMock(), repo_request=AsyncMock(), close=AsyncMock())
        with patch("github_auth.GitHubAPI", return_value=api):
            await self.worker.update_discord_comment("discord_comment_delete", {"message_id": 42})
        api.repo_request.assert_awaited_once_with("DELETE", "issues/comments/88")
        self.worker.api.repo_request.assert_not_awaited()

    async def test_comment_text_cannot_choose_someone_elses_credentials(self):
        async def comments(number):
            yield {"id": 88, "body": "<!-- claimbot-actor:1:999 -->\n<!-- claimbot-discord:42 -->"}
        self.worker.api.comments = comments
        await self.worker.forward_discord_comment(self.payload())
        self.assertEqual(store.get_comment("owner/repo", 88)["actor_user_id"], 0)

    async def test_login_is_available_to_members_without_command_role(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=3), response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        self.auth.allowed_member = AsyncMock(return_value=(self.worker, SimpleNamespace(roles=[])))
        self.auth.oauth.return_value = {"device_code": "secret-code", "user_code": "ABCD-EFGH", "expires_in": 900, "interval": 5}
        await self.auth.start_login(interaction, 1)
        self.assertIn("ABCD-EFGH", interaction.followup.send.call_args.args[0])
        row = store.execute("SELECT * FROM github_device_flows", one=True)
        self.assertNotIn("secret-code", row["credentials"])
        self.assertGreater(row["next_poll"], time.time())
        await self.auth.start_login(interaction, 1)
        self.auth.oauth.assert_awaited_once()

    async def test_device_flow_slow_down_and_success_are_durable(self):
        data = {"device_code": "secret-code", "user_code": "ABCD-EFGH", "repo": "owner/repo", "repo_id": 9}
        store.execute("INSERT INTO github_device_flows VALUES (?, ?, ?, ?, ?, ?)",
                      (1, 3, self.auth.encrypt(data), time.time()+900, 0, 5))
        self.auth.oauth.return_value = {"error": "slow_down", "interval": 10}
        await self.auth.poll_one(store.execute("SELECT * FROM github_device_flows", one=True))
        row = store.execute("SELECT * FROM github_device_flows", one=True)
        self.assertEqual(row["interval"], 10)
        self.assertGreater(row["next_poll"], time.time()+8)
        self.auth.oauth.return_value = {"access_token": "user-secret", "refresh_token": "refresh-secret", "expires_in": 28800, "refresh_token_expires_in": 15897600}
        self.auth.allowed_member = AsyncMock(return_value=(self.worker, SimpleNamespace()))
        api = SimpleNamespace(request=AsyncMock(side_effect=[{"login": "RealAlice"}, {"id": 9}]), close=AsyncMock())
        with patch("github_auth.GitHubAPI", return_value=api):
            await self.auth.poll_one(row)
        self.assertEqual(store.get_link(1, 3)["login"], "RealAlice")
        self.assertIsNone(store.execute("SELECT * FROM github_device_flows", one=True))
        self.assertEqual((await self.auth.token_for(1, 3))["access_token"], "user-secret")
        self.assertEqual(self.auth.oauth.call_args.args[1]["repository_id"], "9")

    async def test_inaccessible_repo_never_records_user_authorization(self):
        data = {"device_code": "code", "repo": "owner/repo", "repo_id": 9}
        store.execute("INSERT INTO github_device_flows VALUES (?, ?, ?, ?, ?, ?)",
                      (1, 3, self.auth.encrypt(data), time.time()+900, 0, 5))
        self.auth.oauth.return_value = {"access_token": "user-secret", "expires_in": 28800}
        self.auth.allowed_member = AsyncMock(return_value=(self.worker, SimpleNamespace()))
        api = SimpleNamespace(request=AsyncMock(side_effect=[{"login": "Alice"}, GitHubError(404,"denied")]), close=AsyncMock())
        with patch("github_auth.GitHubAPI", return_value=api):
            await self.auth.poll_one(store.execute("SELECT * FROM github_device_flows", one=True))
        self.assertIsNone(await self.auth.token_for(1, 3))
        self.assertIsNone(store.get_link(1, 3))

    async def test_logout_clears_pending_login_and_prevents_reconnection(self):
        self.save_auth()
        store.save_link(1,3,"Alice")
        store.execute("INSERT INTO github_device_flows VALUES (?, ?, ?, ?, ?, ?)", (1,3,"encrypted",time.time()+900,0,5))
        interaction = SimpleNamespace(guild_id=1,user=SimpleNamespace(id=3),response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await GitHubAuth.logout.callback(self.auth, interaction)
        self.assertIsNone(await self.auth.token_for(1,3))
        self.assertIsNone(store.get_link(1,3))
        self.assertIsNone(store.execute("SELECT * FROM github_device_flows",one=True))

    async def test_authorized_token_survives_transient_validation_failure(self):
        data = {"device_code": "code", "repo": "owner/repo", "repo_id": 9}
        store.execute("INSERT INTO github_device_flows VALUES (?, ?, ?, ?, ?, ?)",
                      (1,3,self.auth.encrypt(data),time.time()+900,0,5))
        self.auth.oauth.return_value = {"access_token": "user-secret", "expires_in": 28800}
        self.auth.allowed_member = AsyncMock(return_value=(self.worker, SimpleNamespace()))
        api = SimpleNamespace(request=AsyncMock(side_effect=GitHubError(503,"outage")),close=AsyncMock())
        with patch("github_auth.GitHubAPI", return_value=api), self.assertRaises(GitHubError):
            await self.auth.poll_one(store.execute("SELECT * FROM github_device_flows",one=True))
        row=store.execute("SELECT * FROM github_device_flows",one=True)
        self.assertNotIn("user-secret",row["credentials"])
        api.request.side_effect=[{"login":"Alice"},{"id":9}]
        with patch("github_auth.GitHubAPI",return_value=api):
            await self.auth.poll_one(row)
        self.auth.oauth.assert_awaited_once()
        self.assertEqual((await self.auth.token_for(1,3))["access_token"],"user-secret")

    async def test_issue_creation_uses_signed_in_author(self):
        api=SimpleNamespace(repo_request=AsyncMock(return_value=sample_issue()))
        @asynccontextmanager
        async def posting_api(guild_id,user_id):
            yield api,True
        self.worker.posting_api=posting_api
        self.worker.publish=AsyncMock()
        self.worker.confirm=AsyncMock()
        self.worker.api.set_priority=AsyncMock(return_value=sample_issue())
        self.worker.render_mentions=AsyncMock(return_value="Description")
        interaction=SimpleNamespace(guild_id=1,user=SimpleNamespace(id=3),id=99)
        await IssueCog.create.callback(self.worker,interaction,"Title","Description")
        api.repo_request.assert_awaited_once_with("POST","issues",json={"title":"Title","body":"Description"})
        self.worker.api.repo_request.assert_not_awaited()
        self.auth.notify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
