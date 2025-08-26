import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD"))
OWNER_ID = int(os.getenv("OWNER_ID"))  # your Discord user ID

poll_message = None

async def post_poll(channel):
    """Clears channel and posts a new poll."""
    await channel.purge(limit=100)
    msg = await channel.send("React 👍 to vote for server start!")
    await msg.add_reaction("👍")
    return msg

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    global poll_message
    poll_message = await post_poll(channel)

@bot.event
async def on_reaction_add(reaction, user):
    global poll_message
    if user.bot:
        return

    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            await notify_owner()

@bot.command()
async def resetpoll(ctx):
    """Manually reset the poll for the next server session."""
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ You don’t have permission to do this.")
        return

    global poll_message
    poll_message = await post_poll(ctx.channel)
    await ctx.send("✅ Poll has been reset for the next round!")

async def notify_owner():
    """Send an email when vote threshold is reached."""
    import smtplib, ssl

    sender = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    receiver = os.getenv("TO_EMAIL")

    subject = "Server Start Vote Passed!"
    body = "Enough votes have been reached. Time to start the PlayHosting server!"

    message = f"Subject: {subject}\n\n{body}"

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, message)

    print("📧 Email sent!")

bot.run(TOKEN)
