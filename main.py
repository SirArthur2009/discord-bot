# main.py (replace the whole file)
import os
import sys
from dotenv import load_dotenv

# Determine locations
THIS_FILE = os.path.abspath(__file__)
REPO_ROOT = os.path.dirname(THIS_FILE)            # e.g. /app
ALT_PROJECT_DIR = os.path.join(REPO_ROOT, "discord-bot")  # e.g. /app/discord-bot

# Ensure repo root is on sys.path
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# If there's a 'discord-bot' subfolder, also add that (common when you keep project files in that folder)
if os.path.isdir(ALT_PROJECT_DIR) and ALT_PROJECT_DIR not in sys.path:
    sys.path.insert(0, ALT_PROJECT_DIR)

# Load local .env only for development; Railway env vars override process env.
load_dotenv(override=False)

from bot_app import bot

# Now imports from packages under either REPO_ROOT or discord-bot/ should succeed
from cogs.poll import PollCog
from cogs.server import ServerCog
from cogs.roles import RolesCog
from cogs.pause import PauseCog
from cogs.scheduler import SchedulerCog
from cogs.watcher import WatcherCog

# Register cogs
bot.add_cog(PollCog(bot))
bot.add_cog(ServerCog(bot))
bot.add_cog(RolesCog(bot))
bot.add_cog(PauseCog(bot))
bot.add_cog(SchedulerCog(bot))
bot.add_cog(WatcherCog(bot))

# on_ready: re-hook any existing poll message so buttons keep working after restarts
@bot.event
async def on_ready():
    import os
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

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None or TOKEN == "":
    print("❌ DISCORD_TOKEN not set - cannot start bot.")
else:
    bot.run(TOKEN)
