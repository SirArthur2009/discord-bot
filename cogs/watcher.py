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
        if message.channel.id != WATCH_CHANNEL_ID or not message.author.bot:
            return

        for embed in message.embeds:
            if embed.description:
                desc = embed.description.lower()
                serverChat = self.bot.get_channel(SERVER_CHAT_CHANNEL_ID)
                pollChannel = self.bot.get_channel(POLL_CHANNEL_ID)

                # SERVER OPENED
                if "the server has opened" in desc and ":green_circle:" in desc:
                    print("Detected server open event!")
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    # Send credentials to server-chat
                    if serverChat:
                        try:
                            ip = MINECRAFT_SERVER_LOGIN[0] if len(MINECRAFT_SERVER_LOGIN) > 0 else "IP_NOT_SET"
                            port = MINECRAFT_SERVER_LOGIN[1] if len(MINECRAFT_SERVER_LOGIN) > 1 else "PORT_NOT_SET"
                            guild = message.guild
                            role = guild.get_role(GETNOTIFIED_ROLE_ID) if guild else None
                            role_mention = role.mention if role else f"<@&{GETNOTIFIED_ROLE_ID}>"

                            await serverChat.send(
                                "Server is running! ✅\n"
                                f"Use this info to connect to the server:\n"
                                f"IP: {ip}\n"
                                f"Port: {port} (Bedrock users)\n\n"
                                f"{role_mention} — run `!getnotified` in {pollChannel.mention} to be added to notifications."
                            )
                        except Exception as e:
                            print(f"Failed sending server open credentials: {e}")

                    # Update poll message to point to server-chat
                    try:
                        if pollmod.poll_message:
                            await pollmod.poll_message.edit(content=f"✅ Server running — go to {serverChat.mention}", view=None)
                        else:
                            if pollChannel:
                                pollmod.poll_message = await pollChannel.send(f"✅ Server running — go to {serverChat.mention}")
                    except Exception as e:
                        print(f"Failed to update poll message on server open: {e}")

                    # Preserve previous behavior: call running command code path (optional)
                    try:
                        # Use DummyContext to call poll reset / running-style behavior if needed
                        await self.bot.get_cog("ServerCog").running(DummyContext(pollChannel if pollChannel else message.channel))
                    except Exception:
                        pass

                # SERVER SHUTDOWN
                elif "the server has shutdown" in desc and ":red_circle:" in desc:
                    print("Detected server shutdown event!")
                    try:
                        await message.delete()
                    except Exception:
                        pass

                    # Notify server_chat
                    if serverChat:
                        try:
                            await serverChat.send("❌ The server has been shutdown")
                        except Exception as e:
                            print(f"Failed to send shutdown notice to serverChat: {e}")

                    # Restore poll
                    try:
                        # Use the PollCog's resetpoll command via DummyContext
                        try:
                            await self.bot.get_cog("PollCog").resetpoll(DummyContext(pollChannel if pollChannel else message.channel))
                        except Exception:
                            # Fallback: directly call post_poll
                            if pollChannel:
                                pollmod.poll_message = await pollmod.post_poll(pollChannel)
                    except Exception as e:
                        print(f"Failed to reset poll on server shutdown: {e}")
