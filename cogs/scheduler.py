# discord-bot/cogs/scheduler.py
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from discord.ext import tasks, commands
import cogs.poll as pollmod

POLL_PAUSE_HOUR = int(os.getenv("POLL_PAUSE_HOUR", "21"))
POLL_RESUME_HOUR = int(os.getenv("POLL_RESUME_HOUR", "8"))
MT = ZoneInfo("America/Denver")


class SchedulerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # DO NOT start the task in __init__ — event loop not running here

    async def cog_load(self):
        """
        cog_load is called after the cog is added (and awaited) in setup_hook.
        Start the background scheduler here so there's a running event loop.
        """
        if not self.poll_scheduler.is_running():
            self.poll_scheduler.start()

    @tasks.loop(hours=1)
    async def poll_scheduler(self):
        channel = self.bot.get_channel(int(os.getenv("POLL_CHANNEL_ID", "0")))
        if channel is None:
            return

        now = datetime.now(MT).time()

        # Pause at POLL_PAUSE_HOUR
        if now.hour == POLL_PAUSE_HOUR:
            await channel.purge(limit=200, check=lambda m: m.author == self.bot.user)
            pollmod.paused = True
            pollmod.poll_message = None
            await channel.send(f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT.")
            print("🌙 Poll paused for the night.")

        # Resume at POLL_RESUME_HOUR
        elif now.hour == POLL_RESUME_HOUR and not pollmod.running_mode:
            await channel.purge(limit=200, check=lambda m: m.author == self.bot.user)
            pollmod.poll_message = await pollmod.post_poll(channel)
            pollmod.paused = False
            if pollmod.poll_message:
                print("🌅 Morning poll posted automatically.")

    @poll_scheduler.before_loop
    async def before_poll_scheduler(self):
        await self.bot.wait_until_ready()
        now = datetime.now(MT)
        seconds_until_next_hour = (60 - now.minute) * 60 - now.second
        if seconds_until_next_hour <= 0:
            seconds_until_next_hour = 0
        print(f"⏳ Waiting {seconds_until_next_hour} seconds to align scheduler to the hour.")
        await asyncio.sleep(seconds_until_next_hour)
