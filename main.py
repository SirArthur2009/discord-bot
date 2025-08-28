import os
import discord
import asyncio
from discord.ext import commands

# -------- Load environment variables safely --------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID", "0"))
NOTIFY_THREAD_ID = int(os.getenv("NOTIFY_THREAD_ID", "0"))
NOTIFY_ROLE_ID = int(os.getenv("NOTIFY_ROLE_ID", "0"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD", "2"))
OWNER_ID = [int(os.getenv("OWNER_ID_1", "0")), int(os.getenv("OWNER_ID_2", "0"))]
LOGIN_CREDENTIALS = os.getenv("LOGIN_CREDENTIALS").split(", ")

# -------- Intents and Bot --------
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
poll_message = None
running_mode = False  # <-- new flag


# -------- Post poll safely --------
async def post_poll(channel):
    global poll_message
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return None
    try:
        # Only delete the bot’s last poll, not whole channel
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


@bot.event
async def on_reaction_add(reaction, user):
    global poll_message, running_mode
    if user.bot or poll_message is None or running_mode:
        return
    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            await notify_owner()
            await resetAndWait()

            # Only repost if not in running mode
            if not running_mode:
                poll_message = await post_poll(reaction.message.channel)
                print("ℹ️ New poll posted automatically after threshold reached.")


# -------- Commands --------
@bot.command()
async def resetpoll(ctx):
    global poll_message, running_mode
    if ctx.author.id not in OWNER_ID:
        await ctx.send("❌ You don’t have permission to do this.")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = False  # <-- turn running mode off
    await channel.purge(limit=100)
    poll_message = await post_poll(channel)
    if poll_message:
        await ctx.send("✅ Poll has been reset for the next round!")


@bot.command()
async def running(ctx):
    global running_mode, poll_message
    if ctx.author.id not in OWNER_ID:
        await ctx.send("❌ You don’t have permission to do this.")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        await ctx.send("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    running_mode = True
    poll_message = None  # no active poll during running mode
    await channel.purge(limit=100)
    await channel.send("Server is active! ")
    await channel.send(f"Use these credentials to log into the server:\nIP: {LOGIN_CREDENTIALS[0]}\nPort: {LOGIN_CREDENTIALS[1]}")
    await ctx.send("✅ Server credentials posted. Poll will remain paused until !resetpoll is called.")


# -------- Run Bot --------
bot.run(TOKEN)
