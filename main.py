import os
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo
from discord.ui import Button, View

# -------- Load environment variables --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_COOLDOWN = int(os.getenv("VOTE_THRESHOLD", "120"))  # seconds
LOGIN_CREDENTIALS = os.getenv("LOGIN_CREDENTIALS").split(", ")

NOTIFIED_ROLE_ID = int(os.getenv("NOTIFIED_ROLE_ID", "0"))
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))
POLL_PAUSE_HOUR = int(os.getenv("POLL_PAUSE_HOUR", "21"))
POLL_RESUME_HOUR = int(os.getenv("POLL_RESUME_HOUR", "8"))
WATCH_CHANNEL_ID = int(os.getenv("WATCH_CHANNEL_ID", "0"))
SERVER_CHAT_CHANNEL_ID = int(os.getenv("SERVER_CHAT_CHANNEL_ID", "0"))

# -------- Bot and state --------
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

poll_message = None
running_mode = False
paused = False
MT = ZoneInfo("America/Denver")

# -------- Notify owners --------
async def notify_owner(whoAskedName):
    thread = bot.get_channel(NOTIFY_THREAD_ID)
    role_mention = f"<@&{NOTIFY_ROLE_ID}>"

    if thread:
        try:
            await thread.send(f"{role_mention} {whoAskedName} has requested to start the server!")
            await bot.get_channel(CHANNEL_ID).send("📧 Sent notification to owners of server.")
        except Exception as e:
            print(f"❌ Failed to notify owners: {e}")

# -------- Post or update poll --------
async def post_poll(channel):
    global poll_message, running_mode, paused

    if channel is None or running_mode or paused:
        return

    async def button_callback(interaction):
        nonlocal channel
        user = interaction.user

        # Typing indicator while notifying
        async with channel.typing():
            await asyncio.sleep(1)
        await notify_owner(user.name)

        # Disable button and show cooldown
        await interaction.response.edit_message(
            content=f"⏳ Cooldown: wait {VOTE_COOLDOWN//60} minutes before starting again...",
            view=None
        )

        await asyncio.sleep(VOTE_COOLDOWN)

        # Re-enable button if server not running and not paused
        if not running_mode and not paused:
            new_button = Button(label="Start Server", style=discord.ButtonStyle.primary)
            new_button.callback = button_callback
            view = View()
            view.add_item(new_button)
            await poll_message.edit(
                content="📢 **Server Start Poll**\nClick the button to notify owners!",
                view=view
            )

    # Create the button and view
    button = Button(label="Start Server", style=discord.ButtonStyle.primary)
    button.callback = button_callback
    view = View()
    view.add_item(button)

    # Send or edit existing poll message
    if poll_message is None:
        poll_message = await channel.send(
            "📢 **Server Start Poll**\nClick the button to notify owners!",
            view=view
        )
    else:
        await poll_message.edit(
            content="📢 **Server Start Poll**\nClick the button to notify owners!",
            view=view
        )

# -------- Scheduler for night pause / morning resume --------
@tasks.loop(hours=1)
async def poll_scheduler():
    global paused
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    now = datetime.now(MT).time()

    # Pause poll at night
    if now.hour == POLL_PAUSE_HOUR and not paused:
        if poll_message:
            await poll_message.edit(content=f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT", view=None)
        paused = True
        print("🌙 Poll paused for the night.")

    # Resume poll in morning
    elif now.hour == POLL_RESUME_HOUR and paused:
        paused = False
        await post_poll(channel)
        print("🌅 Morning poll resumed.")

@poll_scheduler.before_loop
async def before_poll_scheduler():
    await bot.wait_until_ready()
    now = datetime.now(MT)
    seconds_until_next_hour = 3600 - (now.minute * 60 + now.second)
    await asyncio.sleep(seconds_until_next_hour)

# -------- Detect server start / stop --------
@bot.event
async def on_message(message):
    global running_mode, poll_message

    if message.channel.id == WATCH_CHANNEL_ID and message.author.bot:
        for embed in message.embeds:
            if embed.description:
                desc = embed.description.lower()
                server_chat = bot.get_channel(SERVER_CHAT_CHANNEL_ID)

                if "the server has opened" in desc and ":green_circle:" in desc:
                    running_mode = True
                    if poll_message:
                        await poll_message.edit(content="✅ Server is running!", view=None)
                    if server_chat:
                        await server_chat.send("✅ The server is running")

                elif "the server has shutdown" in desc and ":red_circle:" in desc:
                    running_mode = False
                    if poll_message:
                        await post_poll(bot.get_channel(CHANNEL_ID))  # re-enable button
                    if server_chat:
                        await server_chat.purge(limit=200)
                        await server_chat.send("❌ The server has been shutdown")

    await bot.process_commands(message)

# -------- Bot Ready --------
@bot.event
async def on_ready():
    global poll_message
    print(f"✅ Logged in as {bot.user}")

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Poll channel not found!")
        return

    # Restore poll message if exists
    async for msg in channel.history(limit=50):
        if msg.author == bot.user and "Server Start Poll" in msg.content:
            poll_message = msg
            break

    if poll_message is None:
        await post_poll(channel)

    poll_scheduler.start()
    print("⏰ Scheduler running.")

# -------- Server manual commands --------
@bot.command()
async def running(ctx):
    global running_mode, poll_message
    channel = bot.get_channel(CHANNEL_ID)
    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)
    if not channel:
        await ctx.send("❌ Poll channel not found!")
        return

    running_mode = True
    if poll_message:
        await poll_message.edit(content="✅ Server is running!", view=None)
    await channel.purge(limit=100)
    await channel.send("Server is running!")
    await channel.send(
        f"Use this info to connect:\n"
        f"IP: {LOGIN_CREDENTIALS[0]}\n"
        f"Port: {LOGIN_CREDENTIALS[1]} (Bedrock only)\n"
        f"Role: {role.mention}"
    )

@bot.command()
async def resetpoll(ctx):
    global running_mode
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        await ctx.send("❌ Poll channel not found!")
        return
    running_mode = False
    await post_poll(channel)
    await ctx.send("✅ Poll reset and ready!")

# -------- Run bot --------
bot.run(TOKEN)
