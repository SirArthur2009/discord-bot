import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database

# Import command definitions without reading local credentials or connecting to Discord.
with patch("dotenv.load_dotenv"), patch.dict(os.environ, {"COMMAND_ROLE_ID": "1", "SERVER_CONFIG_PATH": "",
                                                       "GITHUB_TOKEN": "", "GITHUB_CHANNEL_ID": "", "GITHUB_WEBHOOK_SECRET": ""}):
    import bot


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = patch.object(database, "DB_PATH", str(Path(self.directory.name) / "test.db"))
        override.start()
        self.addCleanup(override.stop)
        database.init_db()

    def test_removed_legacy_signup_stays_removed_after_restart(self):
        claim = database.create_claim(1, 2, "legacy", "message", ["option"])
        option = database.get_options(claim)[0]["id"]
        conn = database.db_connect()
        try:
            conn.execute("UPDATE claim_options SET claimed_by = 42 WHERE id = ?", (option,))
            conn.commit()
        finally:
            conn.close()
        database.init_db()
        self.assertEqual(database.get_interests(claim), {option: [42]})
        database.toggle_interest(option, 42)
        database.init_db()
        self.assertEqual(database.get_interests(claim), {})

    def test_multiple_boards_can_wait_for_message_ids(self):
        first = database.create_claim(1, 2, "first", "message", ["option"])
        second = database.create_claim(1, 2, "second", "message", ["option"])
        self.assertNotEqual(database.get_claim_by_id(first)["message_id"],
                            database.get_claim_by_id(second)["message_id"])
        database.set_claim_message_id(first, 100)
        database.set_claim_message_id(second, 101)
        self.assertEqual(database.get_claim_by_id(second)["message_id"], 101)


class ResultsTests(unittest.TestCase):
    def test_large_results_preserve_every_signup_within_limits(self):
        options = [{"id": index, "name": "x" * 300} for index in range(25)]
        interests = {index: list(range(100000000000000000, 100000000000000300))
                     for index in range(25)}
        pages = bot.build_results_embeds({"name": "n" * 300}, options, interests)
        self.assertGreater(len(pages), 1)
        mentions = []
        for page in pages:
            self.assertLessEqual(len(page), 6000)
            self.assertLessEqual(len(page.title), 256)
            self.assertLessEqual(len(page.fields), 25)
            for field in page.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)
                mentions.extend(field.value.split(", "))
        self.assertEqual(mentions, [f"<@{uid}>" for option in options
                                    for uid in interests[option["id"]]])

    def test_empty_option_is_displayed(self):
        pages = bot.build_results_embeds({"name": "board"}, [{"id": 1, "name": "option"}], {})
        self.assertEqual(pages[0].fields[0].value, "No one has signed up yet.")


class AcknowledgmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_management_commands_defer_before_fetching_messages(self):
        for command, mutation in [(bot.claim_reset, "reset_claim"),
                                  (bot.claim_lock_claim, "lock_claim"),
                                  (bot.claim_delete, "delete_claim")]:
            with self.subTest(command=command.name):
                interaction = SimpleNamespace(guild=SimpleNamespace(id=1),
                    response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                    followup=SimpleNamespace(send=AsyncMock()))
                message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())

                async def fetch_message(message_id):
                    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
                    return message

                claim = {"id": 1, "channel_id": 2, "message_id": 3, "locked": False}
                channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=fetch_message))
                with patch.object(bot, "get_claim_by_name", return_value=claim), \
                     patch.object(bot, mutation), \
                     patch.object(bot.bot, "get_channel", return_value=channel), \
                     patch.object(bot, "build_embed"), \
                     patch.object(bot.ClaimView, "from_database"):
                    await command.callback(interaction, "board")
                channel.fetch_message.assert_awaited_once_with(3)
                interaction.followup.send.assert_awaited_once()
                interaction.response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
