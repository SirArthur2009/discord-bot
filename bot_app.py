# discord-bot/bot_app.py
import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
intents.members = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """
        Called by discord.py before connecting. Use this to add cogs asynchronously.
        This avoids 'coroutine was never awaited' and ensures cog_load is called properly.
        """
        # Import cogs here to avoid circular imports at module import time
        from cogs.poll import PollCog
        from cogs.server import ServerCog
        from cogs.roles import RolesCog
        from cogs.pause import PauseCog
        from cogs.scheduler import SchedulerCog
        from cogs.watcher import WatcherCog

        # await add_cog so any async cog_load() runs now (with event loop active)
        await self.add_cog(PollCog(self))
        await self.add_cog(ServerCog(self))
        await self.add_cog(RolesCog(self))
        await self.add_cog(PauseCog(self))
        await self.add_cog(SchedulerCog(self))
        await self.add_cog(WatcherCog(self))


# single bot instance to import elsewhere
bot = MyBot()
