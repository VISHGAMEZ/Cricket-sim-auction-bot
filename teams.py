import discord
from discord.ext import commands
from sqlalchemy import select, insert, update, delete
from database import guilds, teams, players, squad, transactions
from services.core import is_manager, team_for_member, DomainError

class Teams(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def manager(self,ctx):
        if not ctx.guild: raise commands.NoPrivateMessage()
        if await is_manager(self.bot.db,ctx.guild,ctx.author): return True
        raise commands.CheckFailure("Management only")
    @commands.hybrid_command(name="setmanagerrole",description="Authorize the server's management role")
    async def setmanagerrole(self,ctx,role:discord.Role):
        if not ctx.guild or ctx.author.id!=ctx.guild.owner_id: return await ctx.reply("Only the actual server owner can set this role.")
        async with self.bot.db.engine.begin() as c: await c.execute(update(guilds).where(guilds.c.guild_id==ctx.guild.id).values(manager_role_id=role.id))
        await ctx.reply(f"Management role set to {role.mention}.")
    @commands.hybrid_command(name="setauctionchannel",description="Set the server's auction and report channel")
    async def setauctionchannel(self,ctx,channel:discord.TextChannel):
        if not ctx.guild or ctx.author.id!=ctx.guild.owner_id: return await ctx.reply("Only the actual server owner can set the auction channel.")
        async with self.bot.db.engine.begin() as c: await c.execute(update(guilds).where(guilds.c.guild_id==ctx.guild.id).values(auction_channel_id=channel.id))
        await ctx.reply(f"Auction and report channel set to {channel.mention}.")
    @commands.hybrid_command(name="setprefix",description="Change this server's prefix")
    async def setprefix(self,ctx,prefix:str):
        if not ctx.guild or ctx.author.id!=ctx.guild.owner_id: return await ctx.reply("Only the server owner can change the prefix.")
        if not 1<=len(prefix)<=5 or any(ch.isspace() for ch in prefix): return await ctx.reply("Prefix must be 1–5 non-space characters.")
        async with self.bot.db.engine.begin() as c: await c.execute(update(guilds).where(guilds.c.guild_id==ctx.guild.id).values(prefix=prefix))
        await ctx.reply(f"Prefix changed to `{prefix}`. Slash commands are unchanged.")
    @commands.hybrid_command(name="addteam",description="Create a team")
    async def addteam(self,ctx,name:str,purse:int=10000,aliases:str=""):
        if not await self.manager(ctx): return
        if purse<0: return await ctx.reply("Purse cannot be negative.")
        async with self.bot.db.engine.begin() as c:
            from database import tournaments
            running=(await c.execute(select(tournaments.c.id).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.status.in_(["active","paused"])).limit(1))).first()
            if running: return await ctx.reply("End the active auction before changing the configured team list.")
            await c.execute(insert(teams).values(guild_id=ctx.guild.id,name=name,purse=purse,aliases=aliases))
        await ctx.reply(f"Created **{name}** with purse **{purse:,}**.")
    @commands.hybrid_command(name="removeteam",description="Remove a team")
    async def removeteam(self,ctx,team_id:int):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c:
            active=(await c.execute(select(teams.c.id).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))).first()
            if not active: return await ctx.reply("Team not found in this server.")
            from database import tournaments, trades
            running=(await c.execute(select(tournaments.c.id).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.status.in_(["active","paused"])).limit(1))).first()
            if running: return await ctx.reply("End the active auction before removing a team.")
            await c.execute(delete(squad).where(squad.c.guild_id==ctx.guild.id,squad.c.team_id==team_id))
            await c.execute(update(trades).where(trades.c.guild_id==ctx.guild.id,((trades.c.from_team_id==team_id)|(trades.c.to_team_id==team_id)),trades.c.status=="pending").values(status="cancelled"))
            await c.execute(delete(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))
        await ctx.reply("Team removed. Historical auction and wallet records are retained.")
    @commands.hybrid_command(name="teams",description="List configured teams")
    async def teams_cmd(self,ctx):
        if not ctx.guild: return await ctx.reply("Use this in a server.")
        async with self.bot.db.sessions() as s: rows=(await s.execute(select(teams).where(teams.c.guild_id==ctx.guild.id).order_by(teams.c.name))).all()
        if not rows: return await ctx.reply("No teams configured yet.")
        e=discord.Embed(title="Teams",color=discord.Color.blurple())
        for r in rows: e.add_field(name=f"{r.name} · ID {r.id}",value=f"Purse: {r.purse:,}",inline=True)
        await ctx.reply(embed=e)
    async def adjust(self,ctx,team_id,amount,mode):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c:
            t=(await c.execute(select(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id).with_for_update())).first()
            if not t: return await ctx.reply("Team not found.")
            new=amount if mode=="set" else t.purse+amount if mode=="add" else t.purse-amount
            if amount<0: return await ctx.reply("Amount must be zero or greater.")
            if new<0: return await ctx.reply("This would make the purse negative; operation rejected.")
            await c.execute(update(teams).where(teams.c.id==team_id).values(purse=new))
            delta=new-t.purse
            await c.execute(insert(transactions).values(guild_id=ctx.guild.id,team_id=team_id,amount=delta,reason=f"Management {mode}"))
        await ctx.reply(f"{t.name} purse is now **{new:,}**.")
    @commands.hybrid_command(name="setpurse")
    async def setpurse(self,ctx,team_id:int,amount:int): await self.adjust(ctx,team_id,amount,"set")
    @commands.hybrid_command(name="addpurse")
    async def addpurse(self,ctx,team_id:int,amount:int): await self.adjust(ctx,team_id,amount,"add")
    @commands.hybrid_command(name="subtractpurse")
    async def subtractpurse(self,ctx,team_id:int,amount:int): await self.adjust(ctx,team_id,amount,"subtract")
    @commands.hybrid_command(name="setteamrole")
    async def setteamrole(self,ctx,team_id:int,role:discord.Role):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c: r=await c.execute(update(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id).values(owner_role_id=role.id))
        await ctx.reply(f"Team-owner role set for team ID {team_id}." if r.rowcount else "Team not found in this server.")
    @commands.hybrid_command(name="setteamowner")
    async def setteamowner(self,ctx,team_id:int,member:discord.Member):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c: r=await c.execute(update(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id).values(owner_user_id=member.id))
        await ctx.reply(f"{member.mention} assigned as owner of team ID {team_id}." if r.rowcount else "Team not found in this server.")
    @commands.hybrid_command(name="wallet")
    async def wallet(self,ctx,team_id:int|None=None):
        if not ctx.guild: return await ctx.reply("Use this in a server.")
        if team_id is not None and not await is_manager(self.bot.db,ctx.guild,ctx.author): return await ctx.reply("Only management can view another team's wallet.")
        if team_id is None:
            t=await team_for_member(self.bot.db,ctx.guild.id,ctx.author)
            if not t: return await ctx.reply("You are not assigned to a team.")
            await ctx.reply(f"**{t['name']}** available purse: **{t['purse']:,}**")
        else:
            async with self.bot.db.sessions() as s: row=(await s.execute(select(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))).first()
            await ctx.reply(f"**{row.name}** available purse: **{row.purse:,}**" if row else "Team not found.")
    @commands.hybrid_command(name="balance",description="Show your team's available purse")
    async def balance(self,ctx):
        return await self.wallet(ctx,None)
    @commands.hybrid_command(name="addplayer")
    async def addplayer(self,ctx,team_id:int,*,player_name:str):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c:
            team=(await c.execute(select(teams.c.id).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))).first()
            if not team: return await ctx.reply("Team not found in this server.")
            p=(await c.execute(select(players).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)))).first()
            if not p:
                res=await c.execute(insert(players).values(guild_id=ctx.guild.id,name=player_name,category="Unspecified")); pid=res.inserted_primary_key[0]
            else: pid=p.id
            existing=(await c.execute(select(squad).where(squad.c.guild_id==ctx.guild.id,squad.c.player_id==pid))).first()
            if existing: return await ctx.reply("That player is already assigned to a team in this server.")
            await c.execute(insert(squad).values(guild_id=ctx.guild.id,team_id=team_id,player_id=pid,purchase_price=0))
        await ctx.reply(f"Added **{player_name}** to team ID {team_id}.")
    @commands.hybrid_command(name="removeplayer")
    async def removeplayer(self,ctx,team_id:int,*,player_name:str):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c:
            team=(await c.execute(select(teams.c.id).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))).first()
            if not team: return await ctx.reply("Team not found in this server.")
            p=(await c.execute(select(players.c.id).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)))).first()
            if p: await c.execute(delete(squad).where(squad.c.guild_id==ctx.guild.id,squad.c.team_id==team_id,squad.c.player_id==p.id))
        await ctx.reply("Player removed if present.")
    @commands.hybrid_command(name="setplayercategory",description="Set a player's role/category")
    async def setplayercategory(self,ctx,player_name:str,*,category:str):
        if not await self.manager(ctx): return
        async with self.bot.db.engine.begin() as c:
            r=await c.execute(update(players).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)).values(category=category[:80]))
        await ctx.reply("Player category updated." if r.rowcount else "Player not found in this server.")
    @commands.hybrid_command(name="squad",description="Search and view a team squad")
    async def squad_cmd(self,ctx,*,query:str):
        if not ctx.guild: return await ctx.reply("Use this in a server.")
        async with self.bot.db.sessions() as s:
            rows=(await s.execute(select(teams).where(teams.c.guild_id==ctx.guild.id))).all()
        q=query.casefold(); matches=[r for r in rows if q in r.name.casefold() or any(q in a.casefold() for a in r.aliases.split(",") if a)]
        if not matches: return await ctx.reply("No matching teams found.")
        if len(matches)>25: return await ctx.reply("More than 25 teams match. Please enter a more specific team name or alias.")
        if len(matches)>1:
            view=TeamSelectView(self.bot,ctx.guild.id,matches)
            return await ctx.reply("Several teams match. Select one:",view=view)
        await self.show_squad(ctx,matches[0].id)
    async def show_squad(self,ctx,team_id):
        async with self.bot.db.sessions() as s:
            t=(await s.execute(select(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==team_id))).first()
            rows=(await s.execute(select(players.c.name,players.c.category,squad.c.purchase_price).join(squad,players.c.id==squad.c.player_id).where(squad.c.guild_id==ctx.guild.id,squad.c.team_id==team_id))).all()
        if not t: return await ctx.reply("Team not found.")
        body="\n".join(f"• {r.name} — {r.category}" for r in rows) or "No players yet."
        await ctx.reply(embed=discord.Embed(title=f"{t.name} squad ({len(rows)})",description=body+f"\n\nAvailable purse: **{t.purse:,}**",color=discord.Color.green()))

class TeamSelectView(discord.ui.View):
    def __init__(self,bot,guild_id,rows):
        super().__init__(timeout=120); self.bot=bot; self.guild_id=guild_id
        self.select=discord.ui.Select(placeholder="Choose a team",options=[discord.SelectOption(label=r.name[:100],value=str(r.id)) for r in rows[:25]])
        self.add_item(self.select); self.select.callback=self.chosen
    async def chosen(self,interaction):
        cog=self.bot.get_cog("Teams"); await interaction.response.defer()
        await cog.show_squad(InteractionContext(interaction),int(self.select.values[0]))

class InteractionContext:
    def __init__(self,i): self.guild=i.guild; self.author=i.user; self.reply=i.followup.send

async def setup(bot): await bot.add_cog(Teams(bot))
