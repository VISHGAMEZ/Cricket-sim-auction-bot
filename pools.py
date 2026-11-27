import discord
from discord.ext import commands
from sqlalchemy import select, insert, delete, update, func
from database import pools, players, pool_players, guilds
from services.core import is_manager
from players import MASTER_PLAYERS

class Pools(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def allowed(self,ctx):
        if not ctx.guild or not await is_manager(self.bot.db,ctx.guild,ctx.author): raise commands.CheckFailure("Management only")
    @commands.hybrid_command(name="createpool")
    async def createpool(self,ctx,*,name:str):
        await self.allowed(ctx)
        async with self.bot.db.engine.begin() as c: await c.execute(insert(pools).values(guild_id=ctx.guild.id,name=name))
        await ctx.reply(f"Pool **{name}** created.")
    @commands.hybrid_command(name="deletepool")
    async def deletepool(self,ctx,pool_id:int):
        await self.allowed(ctx)
        async with self.bot.db.engine.begin() as c:
            await c.execute(delete(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))
            await c.execute(update(guilds).where(guilds.c.guild_id==ctx.guild.id,guilds.c.active_pool_id==pool_id).values(active_pool_id=None))
        await ctx.reply("Pool deleted if it existed.")
    @commands.hybrid_command(name="addpoolplayer")
    async def addpoolplayer(self,ctx,pool_id:int,*,player_name:str):
        await self.allowed(ctx)
        async with self.bot.db.engine.begin() as c:
            pool=(await c.execute(select(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            if not pool: return await ctx.reply("Pool not found.")
            p=(await c.execute(select(players).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)))).first()
            if not p:
                pid=(await c.execute(insert(players).values(guild_id=ctx.guild.id,name=player_name).returning(players.c.id))).scalar_one()
            else: pid=p.id
            exists=(await c.execute(select(pool_players).where(pool_players.c.pool_id==pool_id,pool_players.c.player_id==pid))).first()
            if exists: return await ctx.reply("Player is already in this pool.")
            pos=(await c.execute(select(func.count()).select_from(pool_players).where(pool_players.c.pool_id==pool_id))).scalar_one()
            await c.execute(insert(pool_players).values(pool_id=pool_id,player_id=pid,position=pos))
        await ctx.reply(f"Added **{player_name}** to pool **{pool.name}**.")
    @commands.hybrid_command(name="removepoolplayer")
    async def removepoolplayer(self,ctx,pool_id:int,*,player_name:str):
        await self.allowed(ctx)
        async with self.bot.db.engine.begin() as c:
            pool=(await c.execute(select(pools.c.id).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            if not pool: return await ctx.reply("Pool not found in this server.")
            p=(await c.execute(select(players.c.id).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)))).first()
            if p: await c.execute(delete(pool_players).where(pool_players.c.pool_id==pool_id,pool_players.c.player_id==p.id))
        await ctx.reply("Player removed from pool if present.")
    @commands.hybrid_command(name="viewpool")
    async def viewpool(self,ctx,pool_id:int):
        await self.allowed(ctx)
        async with self.bot.db.sessions() as s:
            pool=(await s.execute(select(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            rows=(await s.execute(select(players.c.name).join(pool_players,players.c.id==pool_players.c.player_id).where(pool_players.c.pool_id==pool_id,players.c.guild_id==ctx.guild.id).order_by(pool_players.c.position))).all()
        if not pool: return await ctx.reply("Pool not found in this server.")
        await ctx.reply(f"**{pool.name}** (ID {pool_id})\n"+("\n".join(f"{i+1}. {r.name}" for i,r in enumerate(rows)) or "Empty pool."))
    @commands.hybrid_command(name="selectpool")
    async def selectpool(self,ctx,pool_id:int):
        await self.allowed(ctx)
        async with self.bot.db.engine.begin() as c:
            p=(await c.execute(select(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            if not p: return await ctx.reply("Pool not found.")
            await c.execute(update(guilds).where(guilds.c.guild_id==ctx.guild.id).values(active_pool_id=pool_id))
        await ctx.reply(f"Active pool set to **{p.name}**.")
    @commands.hybrid_command(name="importplayers")
    async def importplayers(self,ctx,pool_id:int,*,names:str=""):
        await self.allowed(ctx)
        values=[x.strip() for x in (names.splitlines() if names else MASTER_PLAYERS) if x.strip()]
        async with self.bot.db.engine.begin() as c:
            p=(await c.execute(select(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            if not p: return await ctx.reply("Pool not found.")
            added=0
            for name in values:
                row=(await c.execute(select(players.c.id).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(name)))).first()
                pid=row.id if row else (await c.execute(insert(players).values(guild_id=ctx.guild.id,name=name).returning(players.c.id))).scalar_one()
                exists=(await c.execute(select(pool_players).where(pool_players.c.pool_id==pool_id,pool_players.c.player_id==pid))).first()
                if not exists:
                    pos=(await c.execute(select(func.count()).select_from(pool_players).where(pool_players.c.pool_id==pool_id))).scalar_one()
                    await c.execute(insert(pool_players).values(pool_id=pool_id,player_id=pid,position=pos)); added+=1
        await ctx.reply(f"Imported **{added}** unique players into **{p.name}**.")

async def setup(bot): await bot.add_cog(Pools(bot))
