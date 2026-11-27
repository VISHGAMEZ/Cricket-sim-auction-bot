import discord, time
from discord.ext import commands
from sqlalchemy import select, insert, delete
from database import blacklist

class Admin(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def check(self,ctx):
        if ctx.author.id!=self.bot.config.bot_admin_id: raise commands.CheckFailure("Bot administrator only")
    @commands.hybrid_command(name="blacklist")
    async def blacklist_cmd(self,ctx,owner_user_id:int,*,reason:str=""):
        await self.check(ctx)
        async with self.bot.db.engine.begin() as c:
            await c.execute(delete(blacklist).where(blacklist.c.owner_user_id==owner_user_id))
            await c.execute(insert(blacklist).values(owner_user_id=owner_user_id,reason=reason))
        await ctx.reply(f"Server owner `{owner_user_id}` has been blacklisted globally.")
    @commands.hybrid_command(name="unblacklist")
    async def unblacklist(self,ctx,owner_user_id:int):
        await self.check(ctx)
        async with self.bot.db.engine.begin() as c: r=await c.execute(delete(blacklist).where(blacklist.c.owner_user_id==owner_user_id))
        await ctx.reply("Blacklist entry removed." if r.rowcount else "No blacklist entry found.")
    @commands.hybrid_command(name="servercount")
    async def servercount(self,ctx):
        await self.check(ctx); await ctx.reply(f"Connected to **{len(self.bot.guilds)}** Discord servers.")
    @commands.hybrid_command(name="botstatus")
    async def botstatus(self,ctx):
        await self.check(ctx)
        try:
            async with self.bot.db.engine.connect() as c: await c.exec_driver_sql("SELECT 1")
            db="Connected"
        except Exception: db="Unavailable"
        up=int(time.time()-self.bot.started_at); h,rem=divmod(up,3600); m,s=divmod(rem,60)
        e=discord.Embed(title="Bot status",color=discord.Color.green() if db=="Connected" else discord.Color.red())
        e.add_field(name="State",value="Online",inline=True); e.add_field(name="Uptime",value=f"{h}h {m}m {s}s",inline=True)
        e.add_field(name="Discord",value="Connected" if self.bot.is_ready() else "Disconnected",inline=True)
        e.add_field(name="Latency",value=f"{self.bot.latency*1000:.0f} ms",inline=True); e.add_field(name="Servers",value=str(len(self.bot.guilds)),inline=True); e.add_field(name="Database",value=db,inline=True)
        await ctx.reply(embed=e)

async def setup(bot): await bot.add_cog(Admin(bot))
