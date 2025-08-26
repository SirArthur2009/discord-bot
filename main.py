import discord
from discord.ext import commands
import os, smtplib, ssl

# Load from Railway environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")
POLL_CHANNEL_ID = int(os.getenv("POLL_CHANNEL_ID"))
VOTE_THRESHOLD = int(os.getenv("VOTE_THRESHOLD"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def send_email():
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL, PASSWORD)
        server.sendmail(EMAIL, TO_EMAIL, "Subject: Server Ready\n\nEnough votes were reached!")

@bot.event
async def on_ready():
    print(f"{bot.user} is online!")
    channel = bot.get_channel(POLL_CHANNEL_ID)
    await channel.purge()
    msg = await channel.send("React 👍 to vote for server start!")
    await msg.add_reaction("👍")

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if reaction.emoji == "👍":
        if reaction.count >= VOTE_THRESHOLD:
            send_email()

bot.run(TOKEN)
