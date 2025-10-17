import os
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3

# -------- Load environment variables safely --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "2"))
LOGIN_CREDENTIALS = os.getenv("LOGIN_CREDENTIALS").split(", ")

NOTIFIED_ROLE_ID = int(os.getenv("NOTIFIED_ROLE_ID", "0"))  # Role ID
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))  # Channel restriction by ID
POLL_PAUSE_HOUR = int(os.getenv("POLL_PAUSE_HOUR", "21"))  # 9 PM MT
POLL_RESUME_HOUR = int(os.getenv("POLL_RESUME_HOUR", "8"))
WATCH_CHANNEL_ID = int(os.getenv("WATCH_CHANNEL_ID", "0"))

SERVER_CHAT_CHANNEL_ID = int(os.getenv("SERVER_CHAT_CHANNEL_ID", "0"))

# -------- Extras ---------
unapprovedCommands = ["give"]

# -------- Intents and Bot --------
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
poll_message = None
running_mode = False  # <-- flag to indicate server running
paused = False

# -------- Fixed Mountain Time --------
MT = ZoneInfo("America/Denver")

# -------- Post poll safely --------
from discord.ui import Button, View

async def post_poll(channel):
    """Post a poll with a Start Server button"""
    global poll_message

    if channel is None:
        print("❌ Poll channel not found!")
        return None

    try:
        # Delete previous poll messages
        async for msg in channel.history(limit=50):
            if msg.author == bot.user and "Server Start Poll" in msg.content:
                await msg.delete()

        # Create button
        button = Button(label="Start Server", style=discord.ButtonStyle.primary)

        async def button_callback(interaction):
            user = interaction.user
            # Show typing indicator while "thinking"
            async with channel.typing():
                await asyncio.sleep(1)  # simulate thinking

            await notify_owner(user.name)
            await interaction.response.edit_message(
                content="⏳ Cooldown: wait 2 minutes before starting again...",
                view=None  # remove button during cooldown
            )

            # Wait 2 minutes
            await asyncio.sleep(120)

            # Reset poll (button)
            await post_poll(channel)

        button.callback = button_callback

        view = View()
        view.add_item(button)

        # Send the poll message
        msg = await channel.send("📢 **Server Start Poll**\nClick the button to notify owners!", view=view)
        poll_message = msg
        print(f"✅ Poll posted with button (ID {msg.id})")
        return msg

    except Exception as e:
        print(f"❌ Failed to post poll: {e}")
        return None

# -------- Notify owners via mention roles --------
async def notify_owner(whoAskedName):
    thread = bot.get_channel(NOTIFY_THREAD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role_mention = f"<@&{NOTIFY_ROLE_ID}>"

    if thread is None:
        print("❌ Notify thread not found! Check NOTIFY_THREAD_ID")
        return

    try:
        await thread.send(f"{role_mention} {whoAskedName} has requested to start the server. Please start it when you can. Thank you!")
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
        await asyncio.sleep(120)
        await cooldown_message.delete()

# -------- Night Pause / Morning Resume --------
@tasks.loop(hours=1)
async def poll_scheduler():
    global poll_message, running_mode, paused
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return

    now = datetime.now(MT).time()  # use Mountain Time

    # Pause at 9 PM MT
    if now.hour == POLL_PAUSE_HOUR:
        await channel.purge(limit=100)
        await channel.send(f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT.")
        poll_message = None
        paused = True
        print("🌙 Poll paused for the night.")

    # Resume at 8 AM MT
    elif now.hour == POLL_RESUME_HOUR and not running_mode:
        await channel.purge(limit=100)
        poll_message = await post_poll(channel)
        paused = False
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

# Helper to simulate context for commands
class DummyContext:
    def __init__(self, channel, author=None, guild=None):
        self.channel = channel
        self.author = author or channel.guild.me
        self.guild = guild or channel.guild

    async def send(self, content):
        return await self.channel.send(content)

# -------- Bot Events --------
@bot.event
async def on_message(message):
    #Check for shutdown message
    if message.channel.id == WATCH_CHANNEL_ID and message.author.bot:
        for embed in message.embeds:
            if embed.description:
                dummyContext = DummyContext(message.channel)
                desc = embed.description.lower()
                serverChat = bot.get_channel(SERVER_CHAT_CHANNEL_ID)
    
                if "the server has opened" in desc and ":green_circle:" in desc:
                    print("Detected server open event!")
                    await message.delete()
                    await serverChat.send("✅ The server is running")
                    await running(dummyContext)
    
                elif "the server has shutdown" in desc  and ":red_circle:" in desc:
                    print("Detected server shutdown event!")
                    await message.delete()
                    await serverChat.purge(limit=200)
                    await serverChat.send("❌ The server has been shutdown")
                    await resetpoll(dummyContext)

    await bot.process_commands(message)

@bot.event
async def on_ready():
    global poll_message
    print(f"✅ Logged in as {bot.user}")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return
    if paused == True:
        return

    async for msg in channel.history(limit=50):
        if "React 👍 to vote for server start!" in msg.content or "Server is running!" in msg.content:
            poll_message = msg
            print(f"ℹ️ Found existing message with ID {poll_message.id}")
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
            DaUser = await bot.fetch_user(user.id)  # Fetch the user by ID
            print(f"{DaUser.name} asked to start the poll")
            await notify_owner(DaUser.name)
            await resetAndWait()

            if not running_mode and not paused:
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
    paused = False
    await channel.purge(limit=100)
    poll_message = await post_poll(channel)
    if poll_message:
        await ctx.send("✅ Poll has been reset for the next round!")

@bot.command()
async def running(ctx):
    global running_mode, poll_message

    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)

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
        f"\nMentioning the role {role.mention}. Run !getnotified to get this role and be notified when the server is ready again. Run !stopnotified to remove the role."
    )
    await ctx.send("✅ Server credentials posted. Poll will remain paused until !resetpoll is called.")

@bot.command()
async def pause(ctx):
    global paused

    paused = True
    await ctx.send("⏯️ You have paused the processes!")
    
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
    
    await channel.purge(limit=100)
    await channel.send("⏯️ Processes are now paused! Will resume once !unpause is called")

@bot.command()
async def unpause(ctx):
    global paused

    paused = False

    await ctx.send("⏯️ You have unpaused the processes!")

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
    
    await channel.purge(limit=100)
    poll_message = await post_poll(channel)
    if poll_message:
        await ctx.send("✅ Poll has been reset for the next round!")

# ----------------- getnotified -----------------
@bot.command()
async def getnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Please use this command in the designated channel.")

    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)
    if not role:
        return await ctx.send("The role does not exist!")

    if role in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you already have that role!")

    try:
        await ctx.author.add_roles(role)
        await ctx.send(f"{ctx.author.mention}, you have been added to the role!")
    except discord.Forbidden:
        await ctx.send("I don't have permission to add roles.")

# ----------------- stopnotified -----------------
@bot.command()
async def stopnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Please use this command in the designated channel.")

    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)
    if not role:
        return await ctx.send("The role does not exist!")

    if role not in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you don't have that role!")

    try:
        await ctx.author.remove_roles(role)
        await ctx.send(f"{ctx.author.mention}, the role has been removed.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove roles.")

# -------- Run Bot --------
bot.run(TOKEN)
