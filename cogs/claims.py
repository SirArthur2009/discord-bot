from discord.ext import commands
from utils.database import get_connection

class ClaimsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def pingdb(self, ctx):
        try:
            conn = get_connection()
            if not conn:
                await ctx.send("❌ DB connection failed")
                print("get_connection() returned None")
                return

            cursor = conn.cursor()
            cursor.execute("SELECT NOW();")
            now = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            await ctx.send(f"✅ DB time: `{now}`")
            print("DB query successful:", now)
        except Exception as e:
            await ctx.send(f"❌ Command failed: {e}")
            print("Command exception:", e)

    @commands.command()
    async def testdbvars(self, ctx):
        url = os.getenv("MYSQL_URL")
        if url:
            await ctx.send(f"✅ MYSQL_URL found: `{url}`")
            print("MYSQL_URL:", url)  # This will go to Railway logs
        else:
            await ctx.send("❌ MYSQL_URL not set!")
            print("MYSQL_URL is None")

