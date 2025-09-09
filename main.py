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

async def checkAvailable(firstX, firstZ, secondX, secondZ):
    """Check if the coordinates are available for logging."""
    
    conn = sqlite3.connect('structures.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM structures WHERE (firstX BETWEEN ? AND ?) AND (firstZ BETWEEN ? AND ?) OR (secondX BETWEEN ? AND ?) AND (secondZ BETWEEN ? AND ?)", 
              (firstX, secondX, firstZ, secondZ, firstX, secondX, firstZ, secondZ))
    count = c.fetchone()[0]
    conn.close()
    if count > 0:
        print(f"❌ Coordinates ({firstX}, {firstZ}) to ({secondX}, {secondZ}) are already taken.")
        return False
    print(f"✅ Coordinates ({firstX}, {firstZ}) to ({secondX}, {secondZ}) are available.")
    return True

async def insertStructure(firstX, firstZ, secondX, secondZ, structureName, ownerName):
    """Insert the structure into the database."""
    # Placeholder for actual database insertion logic
    # This should insert the structure data into your SQL database
    print(f"Inserting structure: {structureName} by {ownerName} at ({firstX}, {firstZ}) to ({secondX}, {secondZ})")
    # Example: Use sqlite3 to insert into a table
    conn = sqlite3.connect('structures.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS structures
                 (firstX INTEGER, firstZ INTEGER, secondX INTEGER, secondZ INTEGER,
                  structureName TEXT, ownerName TEXT)''')

    c.execute("INSERT INTO structures (firstX, firstZ, secondX, secondZ, structureName, ownerName) VALUES (?, ?, ?, ?, ?, ?)",     
              (firstX, firstZ, secondX, secondZ, structureName, ownerName))
    
    conn.commit()
    conn.close()

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
    print(f"""Successful flag 1, {str(WATCH_CHANNEL_ID == message.channel.id)}, 
    {str(message.author.bot)}, 
    {str(":green_circle:" in message.content)}, 
    {str("the server has opened" in message.content)},
    Channel from: {message.channel},
    """)
    if message.embeds:
        for embed in message.embeds:
            print("Embed title:", embed.title)
            print("Embed description:", embed.description)
            print("Embed fields:", embed.fields)

    
    if message.author.bot and message.channel.id == WATCH_CHANNEL_ID:
        print("Succesful flag 2" + message.content)

    # Only watch Discord-Linker messages in the watch channel
    if message.author.bot and message.channel.id == WATCH_CHANNEL_ID:
        content = message.content.lower()
        dummy_ctx = DummyContext(message.channel)

        if ":green_circle:" in message.content and "the server has opened" in content:
            await running(dummy_ctx)
        elif ":red_circle:" in message.content and "the server has shutdown" in content:
            await resetpoll(dummy_ctx)

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

@bot.command()
async def logStructure(ctx):
    """This logs structures into a SQL database for querying to know who has what where"""
    structure = ctx.message.content.split(" ", 1)[1]  # Get everything after the command
    structure = structure.split(", ")  # Split by comma and space

    if not structure or len(structure) != 6:
        return await ctx.send("Proper format: !logStructure firstX, firstZ, secondX, secondZ, structureName, ownerName")
    
    try: 
        firstX, firstZ, secondX, secondZ = int(structure[0]), int(structure[1]), int(structure[2]), int(structure[3])
        structureName, ownerName = structure[4], structure[5]
    except Exception as e:
        await ctx.send(f"Incountered exception: {e}, when trying to convert to integer\nBe sure to enter integers and to have six arguments")
        return
    
    if checkAvailable(firstX, firstZ, secondX, secondZ):
        await insertStructure(firstX, firstZ, secondX, secondZ, structureName, ownerName)
        ctx.send(f"✅ Structure '{structureName}' by {ownerName} logged successfully at ({firstX}, {firstZ}) to ({secondX}, {secondZ})")
    else:
        await ctx.send(f"❌ Coordinates ({firstX}, {firstZ}) to ({secondX}, {secondZ}) are already taken. Please choose different coordinates.")

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
