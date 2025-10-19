# discord-bot/cogs/roles.py
import os
from discord.ext import commands

GETNOTIFIED_ROLE_ID = int(os.getenv("GETNOTIFIED_ROLE_ID", "0"))
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))

class RolesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def getnotified(self, ctx):
        if ctx.channel.id != GENERAL_CHANNEL_ID:
            return await ctx.send("Please use this command in the designated channel.")

        role = ctx.guild.get_role(GETNOTIFIED_ROLE_ID)
        if not role:
            return await ctx.send("The role does not exist!")

        if role in ctx.author.roles:
            return await ctx.send(f"{ctx.author.mention}, you already have that role!")

        try:
            await ctx.author.add_roles(role)
            await ctx.send(f"{ctx.author.mention}, you have been added to the role!")
        except discord.Forbidden:
            await ctx.send("I don't have permission to add roles.")

    @commands.command()
    async def stopnotified(self, ctx):
        if ctx.channel.id != GENERAL_CHANNEL_ID:
            return await ctx.send("Please use this command in the designated channel.")

        role = ctx.guild.get_role(GETNOTIFIED_ROLE_ID)
        if not role:
            return await ctx.send("The role does not exist!")

        if role not in ctx.author.roles:
            return await ctx.send(f"{ctx.author.mention}, you don't have that role!")

        try:
            await ctx.author.remove_roles(role)
            await ctx.send(f"{ctx.author.mention}, the role has been removed.")
        except discord.Forbidden:
            await ctx.send("I don't have permission to remove roles.")
