import os
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3
from python_aternos import Client

# -------- Load environment variables --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "2"))
LOGIN_CREDENTIALS = os.getenv("LOGIN_CREDENTIALS").split(", ")
ATERNOS_USER = os.getenv("ATERNOS_USER")
ATERNOS_PASS = os.getenv("ATERNOS_PASS")
NOTIFIED_ROLE_ID = int(os.getenv("NOTIFIED_ROLE_ID"))
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID"))
POLL_PAUSE_HOUR = int(os.getenv("POLL_PAUSE_HOUR", "21"))
POLL_RESUME_HOUR = int(os.getenv("POLL_RESUME_HOUR", "8"))

# -------- Intents and Bot --------
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------- Global state --------
poll_message = None
running_mode = False
paused = False
server_running = False

# -------- Mountain Time --------
MT = ZoneInfo("America/Denver")

# New session-based login:
ATERNOS_SESSION = os.getenv("ATERNOS_SESSION", "").strip()
if not ATERNOS_SESSION:
    raise RuntimeError("❌ Aternos session cookie not set!")

atclient = Client()
try:
    atclient.login_session(ATERNOS_SESSION)
except Exception as e:
    raise RuntimeError(f"❌ Failed to log in to Aternos via session: {e}")

# List servers like before
atservs = atclient.list_servers()
if not atservs:
    raise RuntimeError("❌ No Aternos servers found!")
myserv = atservs[1]

# -------- Database Helpers --------
async def checkAvailable(firstX, firstZ, secondX, secondZ):
    conn = sqlite3.connect('structures.db')
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM structures WHERE "
        "(firstX BETWEEN ? AND ?) AND (firstZ BETWEEN ? AND ?) "
        "OR (secondX BETWEEN ? AND ?) AND (secondZ BETWEEN ? AND ?)",
        (firstX, secondX, firstZ, secondZ, firstX, secondX, firstZ, secondZ)
    )
    count = c.fetchone()[0]
    conn.close()
    return count == 0

async def insertStructure(firstX, firstZ, secondX, secondZ, structureName, ownerName):
    conn = sqlite3.connect('structures.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS structures
                 (firstX INTEGER, firstZ INTEGER, secondX INTEGER, secondZ INTEGER,
                  structureName TEXT, ownerName TEXT)''')
    c.execute("INSERT INTO structures VALUES (?, ?, ?, ?, ?, ?)",
              (firstX, firstZ, secondX, secondZ, structureName, ownerName))
    conn.commit()
    conn.close()

# -------- Post poll --------
async def post_poll(channel):
    global poll_message
    try:
        async for msg in channel.history(limit=50):
            if msg.author == bot.user and "React 👍 to vote" in msg.content:
                await msg.delete()
        msg = await channel.send("React 👍 to vote for server start!")
        await msg.add_reaction("👍")
        poll_message = msg
        return msg
    except Exception as e:
        print(f"❌ Failed to post poll: {e}")
        return None

# -------- Reset Poll / Cooldown --------
async def resetAndWait():
    global poll_message, running_mode, server_running
    channel = bot.get_channel(CHANNEL_ID)
    if not running_mode:
        await channel.purge(limit=100)
        cooldown_msg = await channel.send("⏳ Poll is on cooldown. Wait 1 minute.")
        await asyncio.sleep(60)
        await cooldown_msg.delete()
    server_running = False

# -------- Night Pause / Morning Resume --------
@tasks.loop(hours=1)
async def poll_scheduler():
    global poll_message, running_mode, paused
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None or running_mode or paused:
        return

    now = datetime.now(MT).time()
    if now.hour >= POLL_PAUSE_HOUR:
        await channel.purge(limit=100)
        await channel.send(f"⏸️ Poll paused until {POLL_RESUME_HOUR:02d}:00 MT.")
        poll_message = None
        paused = True
    elif now.hour >= POLL_RESUME_HOUR and not running_mode:
        await channel.purge(limit=100)
        poll_message = await post_poll(channel)
        paused = False

@poll_scheduler.before_loop
async def before_poll_scheduler():
    await bot.wait_until_ready()
    now = datetime.now(MT)
    seconds_until_next_hour = 3600 - (now.minute * 60 + now.second)
    await asyncio.sleep(seconds_until_next_hour)

# -------- Server Monitor --------
@tasks.loop(minutes=10)
async def check_server():
    global server_running
    await bot.wait_until_ready()
    if server_running:
        status = myserv.status
        if status == "offline":
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send("⚠️ Server went offline. Resetting poll...")
            server_running = False
            if check_server.is_running():
                check_server.stop()
            await resetpoll(channel)

# -------- Bot Events --------
@bot.event
async def on_ready():
    global poll_message
    print(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel and not paused:
        async for msg in channel.history(limit=50):
            if "React 👍 to vote for server start!" in msg.content:
                poll_message = msg
                break
        if poll_message is None:
            poll_message = await post_poll(channel)
    poll_scheduler.start()

@bot.event
async def on_reaction_add(reaction, user):
    global poll_message, running_mode, server_running
    if user.bot or poll_message is None or running_mode:
        return
    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send("✅ Vote threshold reached. Starting server...")
            try:
                myserv.start()
                server_running = True
                if not check_server.is_running():
                    check_server.start()
                await running(channel)
            except Exception as e:
                await channel.send(f"❌ Failed to start server: {e}")
            await resetAndWait()

# -------- Commands --------
@bot.command()
async def resetpoll(ctx_or_channel):
    global poll_message, running_mode, server_running, paused
    if isinstance(ctx_or_channel, commands.Context):
        channel = bot.get_channel(CHANNEL_ID)
    else:
        channel = ctx_or_channel
    if not channel:
        return
    running_mode = False
    paused = False
    server_running = False
    await channel.purge(limit=100)
    poll_message = await post_poll(channel)

@bot.command()
async def running(ctx_or_channel):
    global running_mode
    role = None
    if isinstance(ctx_or_channel, commands.Context):
        channel = bot.get_channel(CHANNEL_ID)
        role = ctx_or_channel.guild.get_role(NOTIFIED_ROLE_ID)
    else:
        channel = ctx_or_channel
    running_mode = True
    await channel.purge(limit=100)
    msg = (
        f"Server is running!\n"
        f"IP: {LOGIN_CREDENTIALS[0]}\n"
        f"Port: {LOGIN_CREDENTIALS[1]} (for Bedrock)\n"
        f"\nUse !getnotified to get notified next time."
    )
    if role:
        msg += f"\nMentioning: {role.mention}"
    await channel.send(msg)

# -------- Pause / Unpause --------
@bot.command()
async def pause(ctx):
    global paused
    paused = True
    channel = bot.get_channel(CHANNEL_ID)
    await channel.purge(limit=100)
    await channel.send("⏯️ Processes paused. Use !unpause to resume.")

@bot.command()
async def unpause(ctx):
    global paused
    paused = False
    channel = bot.get_channel(CHANNEL_ID)
    await channel.purge(limit=100)
    await post_poll(channel)
    await channel.send("⏯️ Processes resumed. New poll posted.")

# -------- Structure Logging --------
@bot.command()
async def logStructure(ctx):
    try:
        args = ctx.message.content.split(" ", 1)[1].split(", ")
        firstX, firstZ, secondX, secondZ = map(int, args[:4])
        structureName, ownerName = args[4], args[5]
    except Exception:
        return await ctx.send("Format: !logStructure firstX, firstZ, secondX, secondZ, structureName, ownerName")

    if await checkAvailable(firstX, firstZ, secondX, secondZ):
        await insertStructure(firstX, firstZ, secondX, secondZ, structureName, ownerName)
        await ctx.send(f"✅ Logged '{structureName}' by {ownerName}.")
    else:
        await ctx.send(f"❌ Coordinates taken. Choose different ones.")

# -------- Role Commands --------
@bot.command()
async def getnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Use this command in the designated channel.")
    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)
    if role in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you already have the role!")
    await ctx.author.add_roles(role)
    await ctx.send(f"{ctx.author.mention}, role added.")

@bot.command()
async def stopnotified(ctx):
    if ctx.channel.id != GENERAL_CHANNEL_ID:
        return await ctx.send("Use this command in the designated channel.")
    role = ctx.guild.get_role(NOTIFIED_ROLE_ID)
    if role not in ctx.author.roles:
        return await ctx.send(f"{ctx.author.mention}, you don't have the role!")
    await ctx.author.remove_roles(role)
    await ctx.send(f"{ctx.author.mention}, role removed.")

# -------- Run Bot --------
bot.run(TOKEN)

