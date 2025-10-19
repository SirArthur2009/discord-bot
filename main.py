# discord-bot/main.py
import os
import sys
from dotenv import load_dotenv

# Ensure project root is on sys.path so Python can import 'cogs' and 'utils' packages.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load local .env only for development; Railway env vars override process env.
load_dotenv(override=False)

from bot_app import bot

# Import cogs so they register (these must be importable)
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

# on_ready: re-hook to existing poll message (if present) so buttons work after restart
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

    # Try to find an existing poll message (by content pattern) and re-register the view
    try:
        async for msg in channel.history(limit=200):
            content = (msg.content or "").lower()
            if msg.author == bot.user and ("click the button to vote" in content or "votes:" in content or "server running" in content):
                pollmod.poll_message = msg
                pollmod.poll_votes[msg.id] = set()
                # Re-register the view so component interactions continue to work after restart
                view = pollmod.PollView(message_id=msg.id)
                bot.add_view(view, message_id=msg.id)
                print(f"ℹ️ Found existing poll message (ID {msg.id}) and re-registered view.")
                break
    except Exception as e:
        print(f"Error while scanning channel history for poll message: {e}")

    # keep running: scheduler cog will start itself from its constructor
    print("✅ All cogs loaded and ready.")

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None or TOKEN == "":
    print("❌ DISCORD_TOKEN not set - cannot start bot.")
else:
    bot.run(TOKEN)
