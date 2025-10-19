# discord-bot/main.py
import os
import sys
from dotenv import load_dotenv

# Ensure repo root and discord-bot subdir are on sys.path
THIS_FILE = os.path.abspath(__file__)
REPO_ROOT = os.path.dirname(THIS_FILE)
ALT_PROJECT_DIR = os.path.join(REPO_ROOT, "discord-bot")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.isdir(ALT_PROJECT_DIR) and ALT_PROJECT_DIR not in sys.path:
    sys.path.insert(0, ALT_PROJECT_DIR)

# Load local .env only for dev; Railway env vars override process env.
load_dotenv(override=False)

from bot_app import bot
import os

# on_ready: re-hook any existing poll message so buttons keep working after restarts
@bot.event
async def on_ready():
    import cogs.poll as pollmod

    print(f"✅ Logged in as {bot.user}")

    poll_channel_id = int(os.getenv("POLL_CHANNEL_ID", "0"))
    if poll_channel_id == 0:
        print("❌ POLL_CHANNEL_ID not set")
        return

    channel = bot.get_channel(poll_channel_id)
    if channel is None:
        print("❌ Poll channel not found! Check POLL_CHANNEL_ID")
        return

    # Try to find an existing poll message and re-register its view
    try:
        async for msg in channel.history(limit=200):
            content = (msg.content or "").lower()
            if msg.author == bot.user and ("click the button to vote" in content or "votes:" in content or "server running" in content):
                pollmod.poll_message = msg
                pollmod.poll_votes[msg.id] = set()
                view = pollmod.PollView(message_id=msg.id)
                bot.add_view(view, message_id=msg.id)
                print(f"ℹ️ Found existing poll message (ID {msg.id}) and re-registered view.")
                break
    except Exception as e:
        print(f"Error while scanning channel history for poll message: {e}")

    print("✅ All cogs loaded and ready.")

# Run
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None or TOKEN == "":
    print("❌ DISCORD_TOKEN not set - cannot start bot.")
else:
    bot.run(TOKEN)
