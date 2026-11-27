import discord
from discord.ext import commands
from sqlalchemy import select, insert, update, delete
from database import tournaments, pools, players, teams, passes, bids, auction_results, guilds, squad, unsold_queue
from services.core import is_manager, is_blacklisted, team_for_member, settle_player, start_next_player, start_next_unsold, finalize_if_all_passed, DomainError

async def render_auction(bot, tournament_id):
    async with bot.db.sessions() as s:
        t=(await s.execute(select(tournaments).where(tournaments.c.id==tournament_id))).first()
        if not t: return None
        p=(await s.execute(select(players).where(players.c.id==t.current_player_id))).first() if t.current_player_id else None
        team=(await s.execute(select(teams).where(teams.c.id==t.highest_team_id))).first() if t.highest_team_id else None
    title=p.name if p else "Pool exhausted"
    desc=f"Tournament: **{t.name}**\nStatus: **{t.status.upper()}**\nRound: {t.round_no}"
    if p: desc+=f"\nCurrent bid: **{t.highest_bid:,}**\nHighest team: **{team.name if team else 'No bids'}**"
    return discord.Embed(title=f"IPL Auction — {title}",description=desc,color=discord.Color.gold())

class BidModal(discord.ui.Modal,title="Place your bid"):
    amount=discord.ui.TextInput(label="Bid amount",placeholder="Enter a whole number",max_length=12)
    def __init__(self,view): super().__init__(); self.auction_view=view
    async def on_submit(self,interaction):
        try: amount=int(str(self.amount.value).replace(",",""))
        except ValueError: return await interaction.response.send_message("Enter a valid whole number.",ephemeral=True)
        await self.auction_view.place_bid(interaction,amount)

class AuctionView(discord.ui.View):
    def __init__(self,bot,tournament_id):
        super().__init__(timeout=None); self.bot=bot; self.tournament_id=tournament_id
    @discord.ui.button(label="BID",style=discord.ButtonStyle.success,custom_id="ipl:v3:bid")
    async def bid(self,interaction,button): await interaction.response.send_modal(BidModal(self))
    @discord.ui.button(label="PASS",style=discord.ButtonStyle.secondary,custom_id="ipl:v3:pass")
    async def pass_button(self,interaction,button):
        g=interaction.guild
        if not g: return await interaction.response.send_message("Use this in the auction server.",ephemeral=True)
        if interaction.user.id==g.owner_id and await is_blacklisted(self.bot.db,interaction.user.id): return await interaction.response.send_message("Your account is globally blacklisted from this bot in servers you own.",ephemeral=True)
        t=await team_for_member(self.bot.db,g.id,interaction.user)
        if not t: return await interaction.response.send_message("Only assigned team owners can pass.",ephemeral=True)
        async with self.bot.db.engine.begin() as c:
            a=(await c.execute(select(tournaments).where(tournaments.c.id==self.tournament_id,tournaments.c.guild_id==g.id).with_for_update())).first()
            if not a or a.status!="active" or not a.current_player_id: return await interaction.response.send_message("Auction is not accepting actions.",ephemeral=True)
            if a.highest_team_id==t["id"]: return await interaction.response.send_message("The current highest bidder cannot pass.",ephemeral=True)
            existing=(await c.execute(select(passes).where(passes.c.tournament_id==a.id,passes.c.player_id==a.current_player_id,passes.c.round_no==a.round_no,passes.c.team_id==t["id"]))).first()
            if existing: return await interaction.response.send_message("Your team has already passed on this player this round.",ephemeral=True)
            await c.execute(insert(passes).values(tournament_id=a.id,player_id=a.current_player_id,round_no=a.round_no,team_id=t["id"],user_id=interaction.user.id))
        await interaction.response.send_message("Pass recorded.",ephemeral=True)
        finalized=await finalize_if_all_passed(self.bot.db,self.tournament_id)
        if finalized and interaction.channel: await self.announce_last_result(interaction.channel)
        await self.refresh(interaction)
    async def place_bid(self,interaction,amount):
        g=interaction.guild
        if not g: return await interaction.response.send_message("Use this in the auction server.",ephemeral=True)
        if interaction.user.id==g.owner_id and await is_blacklisted(self.bot.db,interaction.user.id): return await interaction.response.send_message("Your account is globally blacklisted from this bot in servers you own.",ephemeral=True)
        t=await team_for_member(self.bot.db,g.id,interaction.user)
        if not t: return await interaction.response.send_message("Only assigned team owners can bid.",ephemeral=True)
        async with self.bot.db.engine.begin() as c:
            a=(await c.execute(select(tournaments).where(tournaments.c.id==self.tournament_id,tournaments.c.guild_id==g.id).with_for_update())).first()
            if not a or a.status!="active" or not a.current_player_id: return await interaction.response.send_message("Auction is not accepting bids.",ephemeral=True)
            passed=(await c.execute(select(passes).where(passes.c.tournament_id==a.id,passes.c.player_id==a.current_player_id,passes.c.round_no==a.round_no,passes.c.team_id==t["id"]))).first()
            if passed: return await interaction.response.send_message("Your team has passed and cannot bid in this round.",ephemeral=True)
            if a.highest_team_id==t["id"]: return await interaction.response.send_message("Your team already holds the highest bid.",ephemeral=True)
            inc=(await c.execute(select(guilds.c.bid_increment).where(guilds.c.guild_id==g.id))).scalar_one()
            minimum=a.highest_bid+inc
            if amount<minimum: return await interaction.response.send_message(f"Minimum valid bid is **{minimum:,}**.",ephemeral=True)
            current_team=(await c.execute(select(teams).where(teams.c.guild_id==g.id,teams.c.id==t["id"]).with_for_update())).first()
            if not current_team or amount>current_team.purse: return await interaction.response.send_message(f"Your available purse is only **{current_team.purse if current_team else 0:,}**.",ephemeral=True)
            await c.execute(insert(bids).values(tournament_id=a.id,guild_id=g.id,player_id=a.current_player_id,round_no=a.round_no,team_id=t["id"],amount=amount,bidder_id=interaction.user.id))
            await c.execute(update(tournaments).where(tournaments.c.id==a.id).values(highest_bid=amount,highest_team_id=t["id"]))
        await interaction.response.send_message(f"Bid accepted: **{amount:,}** by **{t['name']}**.",ephemeral=True)
        finalized=await finalize_if_all_passed(self.bot.db,self.tournament_id)
        if finalized and interaction.channel: await self.announce_last_result(interaction.channel)
        await self.refresh(interaction)
    async def announce_last_result(self,channel):
        async with self.bot.db.sessions() as s:
            result=(await s.execute(select(auction_results).where(auction_results.c.tournament_id==self.tournament_id).order_by(auction_results.c.id.desc()).limit(1))).first()
            if not result: return
            player=(await s.execute(select(players.c.name).where(players.c.id==result.player_id))).first()
            team=(await s.execute(select(teams.c.name).where(teams.c.id==result.team_id))).first() if result.team_id else None
        if result.result=="SOLD":
            await channel.send(f"🏏 **SOLD — {player.name}** to **{team.name if team else 'Unknown team'}** for **{result.price:,}**.")
        else:
            await channel.send(f"**UNSOLD — {player.name}**. Management can use `reauctionunsold` to return this player to the auction.")
    async def refresh(self,interaction=None):
        embed=await render_auction(self.bot,self.tournament_id)
        view=self if embed and "COMPLETED" not in (embed.description or "") else None
        if interaction and interaction.message:
            try: await interaction.message.edit(embed=embed,view=view)
            except discord.HTTPException: pass
        else:
            async with self.bot.db.sessions() as s: a=(await s.execute(select(tournaments).where(tournaments.c.id==self.tournament_id))).first()
            if a and a.channel_id and a.message_id:
                ch=self.bot.get_channel(a.channel_id)
                if ch:
                    try: msg=await ch.fetch_message(a.message_id); await msg.edit(embed=embed,view=view)
                    except discord.HTTPException: pass

class Auction(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def manager(self,ctx):
        if not ctx.guild or not await is_manager(self.bot.db,ctx.guild,ctx.author): raise commands.CheckFailure("Management only")
    async def post(self,tournament_id,channel):
        embed=await render_auction(self.bot,tournament_id); view=AuctionView(self.bot,tournament_id)
        msg=await channel.send(embed=embed,view=view)
        async with self.bot.db.engine.begin() as c: await c.execute(update(tournaments).where(tournaments.c.id==tournament_id).values(message_id=msg.id,channel_id=channel.id))
        return msg
    @commands.hybrid_command(name="startauction")
    async def startauction(self,ctx,pool_id:int,*,tournament_name:str):
        await self.manager(ctx)
        async with self.bot.db.engine.begin() as c:
            await c.execute(select(guilds).where(guilds.c.guild_id==ctx.guild.id).with_for_update())
            p=(await c.execute(select(pools).where(pools.c.guild_id==ctx.guild.id,pools.c.id==pool_id))).first()
            team_count=(await c.execute(select(teams.c.id).where(teams.c.guild_id==ctx.guild.id))).all()
            active=(await c.execute(select(tournaments.c.id).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.status.in_(["active","paused"])).limit(1))).first()
            if active: return await ctx.reply(f"Tournament `{active.id}` is already active or paused in this server.")
            if not team_count: return await ctx.reply("Create at least one team before starting an auction.")
            if not p: return await ctx.reply("Pool not found.")
            configured_channel=(await c.execute(select(guilds.c.auction_channel_id).where(guilds.c.guild_id==ctx.guild.id))).scalar_one_or_none()
            channel=ctx.guild.get_channel(configured_channel) if configured_channel else ctx.channel
            if channel is None: return await ctx.reply("Configured auction channel is unavailable or inaccessible.")
            tid=(await c.execute(insert(tournaments).values(guild_id=ctx.guild.id,name=tournament_name,pool_id=pool_id,status="active",channel_id=channel.id).returning(tournaments.c.id))).scalar_one()
        pid=await start_next_player(self.bot.db,tid)
        if not pid:
            async with self.bot.db.engine.begin() as c: await c.execute(update(tournaments).where(tournaments.c.id==tid).values(status="completed"))
            return await ctx.reply("The selected pool is empty.")
        try:
            await self.post(tid,channel)
        except discord.Forbidden:
            if channel.id==ctx.channel.id:
                async with self.bot.db.engine.begin() as c: await c.execute(update(tournaments).where(tournaments.c.id==tid).values(status="paused"))
                return await ctx.reply("I cannot send the auction message here. Grant View Channel, Send Messages and Embed Links, then resume the tournament.")
            try:
                await self.post(tid,ctx.channel)
                channel=ctx.channel
            except discord.Forbidden:
                async with self.bot.db.engine.begin() as c: await c.execute(update(tournaments).where(tournaments.c.id==tid).values(status="paused"))
                return await ctx.reply("I cannot send in the configured auction channel or this channel. Grant View Channel, Send Messages and Embed Links, then resume.")
        await ctx.reply(f"Auction **{tournament_name}** started in {channel.mention}. Tournament ID: `{tid}`.")
    @commands.hybrid_command(name="breakauction")
    async def breakauction(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.engine.begin() as c: r=await c.execute(update(tournaments).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.id==tournament_id,tournaments.c.status=="active").values(status="paused"))
        if not r.rowcount: return await ctx.reply("Active tournament not found in this server.")
        await AuctionView(self.bot,tournament_id).refresh()
        await ctx.reply("Auction paused. Current player, bid and passes are preserved.")
    @commands.hybrid_command(name="resumeauction")
    async def resumeauction(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.engine.begin() as c: r=await c.execute(update(tournaments).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.id==tournament_id,tournaments.c.status=="paused").values(status="active"))
        if not r.rowcount: return await ctx.reply("Paused tournament not found in this server.")
        await ctx.reply("Auction resumed; no player was skipped.")
        await AuctionView(self.bot,tournament_id).refresh()
    @commands.hybrid_command(name="endauction")
    async def endauction(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.sessions() as s:
            a=(await s.execute(select(tournaments).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.id==tournament_id))).first()
        if not a: return await ctx.reply("Tournament not found.")
        if a.current_player_id:
            try: await settle_player(self.bot.db,a,"SOLD" if a.highest_team_id else "UNSOLD",a.highest_team_id,a.highest_bid)
            except DomainError as e: return await ctx.reply(str(e))
        async with self.bot.db.engine.begin() as c:
            await c.execute(update(tournaments).where(tournaments.c.id==tournament_id).values(status="completed"))
        report=await self.make_report(tournament_id)
        async with self.bot.db.engine.begin() as c: await c.execute(update(tournaments).where(tournaments.c.id==tournament_id).values(report=report))
        async with self.bot.db.sessions() as s:
            channel_id=(await s.execute(select(guilds.c.auction_channel_id).where(guilds.c.guild_id==ctx.guild.id))).scalar_one_or_none()
        report_channel=ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
        if report_channel is None: report_channel=ctx.channel
        try:
            for page in self.split_report(report,1800): await report_channel.send(page)
        except discord.Forbidden:
            if report_channel.id!=ctx.channel.id:
                for page in self.split_report(report,1800): await ctx.channel.send(page)
                await ctx.reply("The configured report channel was inaccessible; the saved report was posted here instead.")
            else:
                await ctx.reply("Report saved, but I cannot post here. Grant Send Messages permission to the bot.")
        await AuctionView(self.bot,tournament_id).refresh()
        await ctx.reply("Auction ended and the complete report was saved.")
    @staticmethod
    def split_report(report,limit):
        pages=[]; current=""
        for line in report.splitlines():
            if len(current)+len(line)+1>limit and current:
                pages.append(current); current=""
            current+=("\n" if current else "")+line
        if current: pages.append(current)
        return pages
    async def make_report(self,tid):
        async with self.bot.db.sessions() as s:
            a=(await s.execute(select(tournaments).where(tournaments.c.id==tid))).first()
            ts=(await s.execute(select(teams).where(teams.c.guild_id==a.guild_id))).all()
            out=[f"🏆 **{a.name} — Auction Report**"]
            for t in ts:
                rows=(await s.execute(select(players.c.name,squad.c.purchase_price).join(squad,players.c.id==squad.c.player_id).where(squad.c.guild_id==a.guild_id,squad.c.team_id==t.id))).all()
                spend=sum(r.purchase_price for r in rows)
                out.append(f"\n**{t.name}** — {len(rows)} players | Spent: {spend:,} | Remaining: {t.purse:,}")
                out.extend(f"• {r.name} — {r.purchase_price:,}" for r in rows)
        return "\n".join(out)
    @commands.hybrid_command(name="nextplayer")
    async def nextplayer(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.engine.begin() as c:
            a=(await c.execute(select(tournaments).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.id==tournament_id).with_for_update())).first()
            if not a or a.current_player_id: return await ctx.reply("Finalize the current player with SOLD or UNSOLD first.")
        pid=await start_next_player(self.bot.db,tournament_id)
        await AuctionView(self.bot,tournament_id).refresh()
        await ctx.reply("Next player selected." if pid else "Pool exhausted.")
    async def manual_result(self,ctx,tid,result):
        await self.manager(ctx)
        async with self.bot.db.sessions() as s: a=(await s.execute(select(tournaments).where(tournaments.c.guild_id==ctx.guild.id,tournaments.c.id==tid))).first()
        if not a or not a.current_player_id: return await ctx.reply("No current player.")
        try: done=await settle_player(self.bot.db,a,result,a.highest_team_id,a.highest_bid)
        except DomainError as e: return await ctx.reply(str(e))
        if not done: return await ctx.reply("This player round was already finalized.")
        view=AuctionView(self.bot,tid)
        await view.announce_last_result(ctx.channel)
        pid=await start_next_player(self.bot.db,tid); await view.refresh()
        await ctx.reply(f"Player marked {result}."+(" Next player loaded." if pid else " Pool exhausted."))
    @commands.hybrid_command(name="sold")
    async def sold(self,ctx,tournament_id:int): await self.manual_result(ctx,tournament_id,"SOLD")
    @commands.hybrid_command(name="unsold")
    async def unsold(self,ctx,tournament_id:int): await self.manual_result(ctx,tournament_id,"UNSOLD")
    @commands.hybrid_command(name="reauctionunsold",description="Return the next unsold player to the auction")
    async def reauctionunsold(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.sessions() as s:
            own=(await s.execute(select(tournaments.c.id).where(tournaments.c.id==tournament_id,tournaments.c.guild_id==ctx.guild.id))).first()
        if not own: return await ctx.reply("Tournament not found in this server.")
        pid=await start_next_unsold(self.bot.db,tournament_id)
        if not pid: return await ctx.reply("No unsold players are waiting, or this tournament is not ready for another player.")
        await AuctionView(self.bot,tournament_id).refresh()
        await ctx.reply("Next unsold player queued for a new bidding round. Passes are reset.")
    @commands.hybrid_command(name="unsoldplayers",description="List players waiting for an unsold reauction")
    async def unsoldplayers(self,ctx,tournament_id:int):
        await self.manager(ctx)
        async with self.bot.db.sessions() as s:
            own=(await s.execute(select(tournaments.c.id).where(tournaments.c.id==tournament_id,tournaments.c.guild_id==ctx.guild.id))).first()
            if not own: return await ctx.reply("Tournament not found in this server.")
            rows=(await s.execute(select(players.c.name).join(unsold_queue,players.c.id==unsold_queue.c.player_id).where(unsold_queue.c.tournament_id==tournament_id,players.c.guild_id==ctx.guild.id).order_by(unsold_queue.c.position))).all()
        await ctx.reply("Unsold queue:\n"+("\n".join(f"• {r.name}" for r in rows) or "No players waiting."))
    @commands.hybrid_command(name="auctionreport",description="View a saved auction report")
    async def auctionreport(self,ctx,tournament_id:int):
        if not ctx.guild: