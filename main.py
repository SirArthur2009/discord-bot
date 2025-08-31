import os
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo

# -------- Load environment variables safely --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "2"))
LOGIN_CREDENTIALS = os.getenv("LOGIN_CREDENTIALS").split(", ")

NOTIFIED_ROLE_NAME = "get-notified"  # Role to assign
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID"))  # Channel restriction by ID


# -------- Intents and Bot --------
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
poll_message = None
running_mode = False  # <-- flag to indicate server running

# -------- Fixed Mountain Time --------
MT = ZoneInfo("America/Denver")
POLL_PAUSE_HOUR = 21  # 9 PM MT
POLL_RESUME_HOUR = 8  # 8 AM MT


# -------- Post poll safely --------
async def post_poll(channel):
    global poll_message
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return None
    try:
        # Delete only the bot's last poll
        async for msg in channel.history(limit=50):
            if msg.author == bot.user and "React 👍 to vote" in msg.content:
                await msg.delete()
        msg = await channel.send("React 👍 to vote for server start!")
        await msg.add_reaction("👍")
        print(f"✅ Poll posted with ID {msg.id}")
        poll_message = msg
        return msg
    except Exception as e:
        print(f"❌ Failed to post poll: {e}")
        return None

# -------- Notify owners via mention roles --------
async def notify_owner():
    thread = bot.get_channel(NOTIFY_THREAD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role_mention = f"<@&{NOTIFY_ROLE_ID}>"

    if thread is None:
        print("❌ Notify thread not found! Check NOTIFY_THREAD_ID")
        return

    try:
        await thread.send(f"{role_mention} ✅ Enough votes have been reached! Time to start the server!")
        await channel.send("📧 Sent notification to owners of server.")
        print("📧 Notification sent in Discord thread!")
    except Exception as e:
        print(f"❌ Failed to send notification in thread: {e}")

# -------- Reset and wait placeholder --------
async def resetAndWait():
    global poll_message, running_mode
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        print("❌ Poll channel not found in resetAndWait()")
        return

    # Only do cooldown if not in running mode
    if not running_mode:
        await channel.purge(limit=100)
        cooldown_message = await channel.send("⏳ Poll is on cooldown. Please wait 1 minute before voting again.")
        await asyncio.sleep(60)
        await cooldown_message.delete()

# -------- Night Pause / Morning Resume --------
@tasks.loop(hours=1)
async def poll_scheduler():
    global poll_message, running_mode
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return

    now = datetime.now(MT).time()  # use Mountain Time

    # Pause at 9 PM MT
    if now.hour == POLL_PAUSE_HOUR:
        await channel.purge(limit=100)
        await channel.send(f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT.")
        poll_message = None
        print("🌙 Poll paused for the night.")

    # Resume at 8 AM MT
    elif now.hour == POLL_RESUME_HOUR and not running_mode:
        await channel.purge(limit=100)
        poll_message = await post_poll(channel)
        if poll_message:
            print("🌅 Morning poll posted automatically.")

@poll_scheduler.before_loop
async def before_poll_scheduler():
    """Wait until the top of the next hour before starting the loop."""
    await bot.wait_until_ready()
    now = datetime.now(MT)
    seconds_until_next_hour = 3600 - (now.minute * 60 + now.second)
    print(f"⏳ Waiting {seconds_until_next_hour} seconds to align scheduler to the hour.")
    await asyncio.sleep(seconds_until_next_hour)


# -------- Bot Events --------
@bot.event
async def on_ready():
    global poll_message
    print(f"✅ Logged in as {bot.user}")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    async for msg in channel.history(limit=50):
        if "React 👍 to vote for server start!" in msg.content:
            poll_message = msg
            print(f"ℹ️ Found existing poll with ID {poll_message.id}")
            break

    if poll_message is None:
        poll_message = await post_poll(channel)
        print("ℹ️ Posted a fresh poll on startup.")

    # Start the scheduler
    poll_scheduler.start()
    print(f"⏰ Scheduler running in Mountain Time (pause {POLL_PAUSE_HOUR:02d}:00, resume {POLL_RESUME_HOUR:02d}:00)")

@bot.event
async def on_reaction_add(reaction, user):
    global poll_message, running_mode
    if user.bot or poll_message is None or running_mode:
        return
    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            await notify_owner()
            await resetAndWait()

            if not running_mode:
                poll_message = await post_poll(reaction.message.channel)
                print("ℹ️ New poll posted automatically after threshold reached.")

# -------- Commands --------
@bot.command()
async def resetpoll(ctx):
    global poll_message, running_mode

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = False
    await channel.purge(limit=100)
    poll_message = await post_poll(channel)
    if poll_message:
        await ctx.send("✅ Poll has been reset for the next round!")


@bot.command()
async def running(ctx):
    global running_mode, poll_message

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = True
    poll_message = None
    await channel.purge(limit=100)
    await channel.send("Server is running! ")
    await channel.send(
        f"Use this info to connect to the server:\n"
        f"IP: {LOGIN_CREDENTIALS[0]}\n"
        f"Port: {LOGIN_CREDENTIALS[1]} (The port is for Bedrock users only)\n"
        f"\nMentioning the role <@&{NOTIFIED_ROLE_NAME}>. Run !getnotified to get this role and be notified when the server is ready again. Run !stopnotified to remove the role."
    )
    await ctx.send("✅ Server credentials posted. Poll will remain paused until !resetpoll is called.")



# ----------------- getnotified command -----------------
@bot.command()
async def getnotified(ctx):
    # Restrict to a specific channel by ID
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send(f"Please use this command in the designated channel.")

    role = discord.utils.get(ctx.guild.roles, name=NOTIFIED_ROLE_NAME)
    if not role:
        return await ctx.send(f"The role **{NOTIFIED_ROLE_NAME}** does not exist!")

    if role in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you already have the **{NOTIFIED_ROLE_NAME}** role!")

    try:
        await ctx.author.add_roles(role)
        await ctx.send(f"{ctx.author.mention}, you have been added to the **{NOTIFIED_ROLE_NAME}** role!")
    except discord.Forbidden:
        await ctx.send("I don't have permission to add roles.")

# ----------------- stopnotified command -----------------
@bot.command()
async def stopnotified(ctx):
    # Restrict to a specific channel by ID
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send(f"Please use this command in the designated channel.")

    role = discord.utils.get(ctx.guild.roles, name=NOTIFIED_ROLE_NAME)
    if not role:
        return await ctx.send(f"The role **{NOTIFIED_ROLE_NAME}** does not exist!")

    if role not in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you don't have the **{NOTIFIED_ROLE_NAME}** role!")

    try:
        await ctx.author.remove_roles(role)
        await ctx.send(f"{ctx.author.mention}, the **{NOTIFIED_ROLE_NAME}** role has been removed.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove roles.")


# -------- Run Bot --------
bot.run(TOKEN)
