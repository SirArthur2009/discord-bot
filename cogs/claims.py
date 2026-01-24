from discord.ext import commands
from utils.database import get_connection

bot = commands.Bot(command_prefix="!")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def pingdb(ctx):
    conn = get_connection()
    if not conn:
        await ctx.send("❌ Database connection failed")
        return

    cursor = conn.cursor()
    cursor.execute("SELECT NOW();")
    time = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    await ctx.send(f"✅ Database time: `{time}`")