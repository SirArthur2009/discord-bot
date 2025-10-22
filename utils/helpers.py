# discord-bot/utils/helpers.py
import os
from bot_app import bot
import aternos

NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
EDITING = os.getenv("EDITING_MODE", "false").lower() == "true"

async def notify_owner_thread(whoAskedName: str):
    """
    Sends the owner notification into the configured thread.
    Does NOT send any confirmation into the poll channel (poll message will be edited instead).
    """
    thread = bot.get_channel(NOTIFY_THREAD_ID)
    role_mention = f"<@&{NOTIFY_ROLE_ID}>" if not EDITING else "[Editing Mode - No Role Mention]"
    if thread is None:
        print("❌ Notify thread not found! Check NOTIFY_THREAD_ID")
        return
    try:
        await thread.send(f"{role_mention} {whoAskedName} has requested to start the server. Please start it when you can. Thank you!")
        print("📧 Notification sent in Discord thread!")
    except Exception as e:
        print(f"❌ Failed to send notification in thread: {e}")


class DummyContext:
    """Lightweight context to call command functions from non-command code."""
    def __init__(self, channel, author=None, guild=None):
        self.channel = channel
        self.author = author or channel.guild.me
        self.guild = guild or channel.guild

    async def send(self, content):
        return await self.channel.send(content)

class AternosAPI:
    def __init__(self, channel):
        self.channel = channel

    # Starts the specified server
    def startServ(aternos):
        aternos.start()

    # Stops the specified server
    def stopServ(aternos):
        aternos.stop()
        
