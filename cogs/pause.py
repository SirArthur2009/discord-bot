# discord-bot/cogs/pause.py
import os
from discord.ext import commands
import cogs.poll as pollmod
from utils.helpers import DummyContext

class PauseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def pause(self, ctx):
        global pollmod
        pollmod.paused = True
        await ctx.send("⏯️ You have paused the processes!")

        channel = self.bot.get_channel(int(os.getenv("POLL_CHANNEL_ID", "0")))
        if channel is None:
            await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
            return

        await channel.purge(limit=200, check=lambda m: m.author == self.bot.user)

        if pollmod.poll_message:
            view = pollmod.PollView(message_id=pollmod.poll_message.id)
            for item in view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            try:
                await pollmod.poll_message.edit(content="⏯️ Processes are now paused! Will resume once !unpause is called", view=view)
            except Exception:
                pass
        else:
            await channel.send("⏯️ Processes are now paused! Will resume once !unpause is called")

    @commands.command()
    async def unpause(self, ctx):
        global pollmod
        pollmod.paused = False
        await ctx.send("⏯️ You have unpaused the processes!")

        channel = self.bot.get_channel(int(os.getenv("POLL_CHANNEL_ID", "0")))
        if channel is None:
            await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
            return

        await channel.purge(limit=200, check=lambda m: m.author == self.bot.user)
        poll_message_local = await pollmod.post_poll(channel)
        if poll_message_local:
            await ctx.send("✅ Poll has been reset for the next round!")
