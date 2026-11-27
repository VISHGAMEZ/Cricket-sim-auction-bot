import os, logging, time
from dotenv import load_dotenv
import discord
from discord.ext import commands
from config import Config
from database import Database

load_dotenv()
config=Config.from_env()
logging.basicConfig(level=getattr(logging,config.log_level.upper(),logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger("ipl-auction")

class AuctionBot(commands.Bot):
    def __init__(self):
        intents=discord.Intents.default(); intents.guilds=True; intents.members=True; intents.message_content=True
        super().__init__(command_prefix=self.prefix_for,intents=intents,help_command=None)
        self.config=config; self.db=Database(config.database_url); self.started_at=time.time()
        self.add_check(self.guild_access_check)
        self.tree.interaction_check=self.tree_access_check
        self.tree.on_error=self.tree_error
    async def guild_access_check(self,ctx):
        if ctx.guild:
            from services.core import is_blacklisted
            if ctx.author.id==ctx.guild.owner_id and await is_blacklisted(self.db,ctx.author.id):
                raise commands.CheckFailure("Your account is globally blacklisted from using this bot in servers you own.")
        return True
    async def tree_error(self,interaction,error):
        log.exception("Slash command error",exc_info=error)
        message="The command could not be completed. Check your permissions and arguments, then try again."
        try:
            if interaction.response.is_done(): await interaction.followup.send(message,ephemeral=True)
            else: await interaction.response.send_message(message,ephemeral=True)
        except discord.HTTPException: pass
    async def tree_access_check(self,interaction):
        if interaction.guild:
            from services.core import is_blacklisted
            if interaction.user.id==interaction.guild.owner_id and await is_blacklisted(self.db,interaction.user.id):
                await interaction.response.send_message("Your account is globally blacklisted from using this bot in servers you own.",ephemeral=True)
                return False
        return True
    async def prefix_for(self, message):
        if not message.guild: return commands.when_mentioned_or(config.default_prefix)(self,message)
        return commands.when_mentioned_or(await self.db.get_prefix(message.guild.id,config.default_prefix))(self,message)
    async def setup_hook(self):
        await self.db.init()
        for ext in ("cogs.general","cogs.teams","cogs.pools","cogs.auction","cogs.trades","cogs.admin"):
            await self.load_extension(ext)
        # Restore persistent views for records that survive a restart.
        from cogs.auction import AuctionView
        from cogs.trades import TradeView
        async with self.db.sessions() as s:
            from sqlalchemy import select
            from database import tournaments, trades
            active=(await s.execute(select(tournaments).where(tournaments.c.status.in_(["active","paused"]),tournaments.c.current_player_id.is_not(None)))).all()
            pending=(await s.execute(select(trades).where(trades.c.status=="pending"))).all()
        for t in active: self.add_view(AuctionView(self,t.id), message_id=t.message_id)
        for tr in pending: self.add_view(TradeView(self,tr.id), message_id=tr.message_id)
        try: await self.tree.sync()
        except discord.HTTPException: log.exception("Slash command sync failed")
    async def on_ready(self):
        log.info("Connected as %s; guilds=%s",self.user,len(self.guilds))
        for g in self.guilds: await self.db.ensure_guild(g.id,config.default_prefix)
        from cogs.auction import AuctionView
        async with self.db.sessions() as s:
            from sqlalchemy import select
            from database import tournaments
            active=(await s.execute(select(tournaments.c.id).where(tournaments.c.status.in_(["active","paused"]),tournaments.c.current_player_id.is_not(None)))).all()
        for row in active: await AuctionView(self,row.id).refresh()
    async def on_guild_join(self,guild):
        await self.db.ensure_guild(guild.id,config.default_prefix)
    async def close(self):
        await self.db.close(); await super().close()
    async def on_command_error(self,ctx,error):
        if isinstance(error,commands.CommandNotFound): return
        if isinstance(error,commands.MissingRequiredArgument): msg=f"Missing argument: `{error.param.name}`. Use `{ctx.prefix}help`."
        elif isinstance(error,commands.CheckFailure): msg="You don't have permission to use that command."
        elif isinstance(error,commands.BadArgument): msg=str(error)
        else: log.exception("Command error",exc_info=error); msg="Something went wrong. Please try again or contact management."
        try: await ctx.send(msg)
        except discord.HTTPException: pass

bot=AuctionBot()
bot.run(config.token)
