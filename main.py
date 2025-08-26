import os
import discord
from discord.ext import commands, tasks

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD"))
poll_message = None

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    channel = bot.get_channel(CHANNEL_ID)

    # 1. Clear old messages
    await channel.purge(limit=100)

    # 2. Post the poll
    global poll_message
    poll_message = await channel.send("React 👍 to vote for server start!")
    await poll_message.add_reaction("👍")

@bot.event
async def on_reaction_add(reaction, user):
    global poll_message
    if user.bot:
        return

    if reaction.message.id == poll_message.id and str(reaction.emoji) == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            await notify_owner()

async def notify_owner():
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

