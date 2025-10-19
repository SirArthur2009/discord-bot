# discord-bot/cogs/server.py
import os
from discord.ext import commands
import discord
import cogs.poll as pollmod

MINECRAFT_SERVER_LOGIN = os.getenv("LOGIN_CREDENTIALS", "IP NOT FOUND, PORT NOT FOUND").split(",")
GETNOTIFIED_ROLE_ID = int(os.getenv("GETNOTIFIED_ROLE_ID", "0"))

class ServerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def running(self, ctx):
        """
        Manual command to mark the server running:
        - posts credentials to server chat
        - updates poll message to point to server-chat (and disables buttons)
        """
        global pollmod
        poll_channel = self.bot.get_channel(int(os.getenv("POLL_CHANNEL_ID", "0")))
        server_chat = self.bot.get_channel(int(os.getenv("SERVER_CHAT_CHANNEL_ID", "0")))

        if poll_channel is None or server_chat is None:
            await ctx.send("❌ Poll or server chat channel not found! Check env vars.")
            return

        pollmod.running_mode = True

        # Update poll message to point users to server-chat (remove buttons)
        try:
            if pollmod.poll_message:
                await pollmod.poll_message.edit(content=f"✅ Server running — go to {server_chat.mention}", view=None)
            else:
                tmp = await poll_channel.send(f"✅ Server running — go to {server_chat.mention}")
                pollmod.poll_message = tmp
        except Exception as e:
            print(f"Failed to update poll message on running(): {e}")

        # Send credentials to server chat
        try:
            ip = MINECRAFT_SERVER_LOGIN[0] if len(MINECRAFT_SERVER_LOGIN) > 0 else "IP_NOT_SET"
            port = MINECRAFT_SERVER_LOGIN[1] if len(MINECRAFT_SERVER_LOGIN) > 1 else "PORT_NOT_SET"
            guild = ctx.guild
            role = guild.get_role(GETNOTIFIED_ROLE_ID) if guild else None
            role_mention = role.mention if role else f"<@&{GETNOTIFIED_ROLE_ID}>"

            await server_chat.send(
                "Server is running! ✅\n"
                f"Use this info to connect to the server:\n"
                f"IP: {ip}\n"
                f"Port: {port} (Bedrock users)\n\n"
                f"{role_mention} — run `!getnotified` in {poll_channel.mention} to be added to notifications."
            )
        except Exception as e:
            print(f"Failed to send credentials to server_chat: {e}")

        try:
            await ctx.send("✅ Server credentials posted to server chat and poll updated.")
        except Exception:
            pass
