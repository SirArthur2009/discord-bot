# discord-bot/cogs/watcher.py
import os
from discord.ext import commands
import cogs.poll as pollmod
from utils.helpers import DummyContext

WATCH_CHANNEL_ID = int(os.getenv("WATCH_CHANNEL_ID", "0"))
SERVER_CHAT_CHANNEL_ID = int(os.getenv("SERVER_CHAT_CHANNEL_ID", "0"))
GETNOTIFIED_ROLE_ID = int(os.getenv("GETNOTIFIED_ROLE_ID", "0"))
POLL_CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
MINECRAFT_SERVER_LOGIN = os.getenv("LOGIN_CREDENTIALS", "IP NOT FOUND, PORT NOT FOUND").split(",")

class WatcherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Only respond to bot messages posted in the WATCH_CHANNEL
        print(WATCH_CHANNEL_ID==message.channel.id)
        if message.channel.id != WATCH_CHANNEL_ID or not message.author.bot:
            return

        for embed in message.embeds:
            if embed.description:
                desc = embed.description.lower()
                print(f"WatcherCog detected embed description: {desc}")
                serverChat = self.bot.get_channel(SERVER_CHAT_CHANNEL_ID)
                pollChannel = self.bot.get_channel(POLL_CHANNEL_ID)

                # SERVER OPENED
                if "the server has started!" in desc and ":green_circle:" in desc:
                    print("Detected server open event!")
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    # Preserve previous behavior: call running command code path (optional)
                    try:
                        # Use DummyContext to call poll reset / running-style behavior if needed
                        await self.bot.get_cog("ServerCog").running(DummyContext(pollChannel if pollChannel else message.channel))
                    except Exception:
                        pass

                # SERVER SHUTDOWN
                elif "the server has stopped!" in desc and ":red_circle:" in desc:
                    print("Detected server shutdown event!")
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    try:
                        await serverChat.purge(limit=100)
                        await serverChat.send("❌ The server has been shutdown")
                    except Exception as e:
                        print(f"Failed to send shutdown notice to serverChat: {e}")

                    # Restore poll
                    try:
                        # Use the PollCog's resetpoll command via DummyContext
                        try:
                            await self.bot.get_cog("PollCog").resetpoll(DummyContext(pollChannel if pollChannel else message.channel))
                        except Exception as e:
                            # Fallback: directly call post_poll
                            print(f"Failed to reset poll via command, falling back: {e}")
                            if pollChannel:
                                pollmod.poll_message = await pollmod.post_poll(pollChannel)
                    except Exception as e:
                        print(f"Failed to reset poll on server shutdown: {e}")
