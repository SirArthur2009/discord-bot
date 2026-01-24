from discord.ext import commands
from utils.database import get_connection

class ClaimsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def pingdb(self, ctx):
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