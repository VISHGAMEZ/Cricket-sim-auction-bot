import discord
from discord.ext import commands
from sqlalchemy import select, insert, update
from database import teams, players, squad, trades
from services.core import is_blacklisted, team_for_member

async def process_trade(bot,trade_id):
    async with bot.db.engine.begin() as c:
        tr=(await c.execute(select(trades).where(trades.c.id==trade_id).with_for_update())).first()
        if not tr or tr.status!="pending" or not tr.from_approved or not tr.to_approved: return False
        sender=(await c.execute(select(squad).where(squad.c.guild_id==tr.guild_id,squad.c.team_id==tr.from_team_id,squad.c.player_id==tr.send_player_id).with_for_update())).first()
        if not sender:
            await c.execute(update(trades).where(trades.c.id==trade_id).values(status="cancelled")); return False
        receiver=(await c.execute(select(squad).where(squad.c.guild_id==tr.guild_id,squad.c.team_id==tr.to_team_id,squad.c.player_id==tr.receive_player_id).with_for_update())).first() if tr.receive_player_id else None
        if tr.receive_player_id and not receiver:
            await c.execute(update(trades).where(trades.c.id==trade_id).values(status="cancelled")); return False
        to_team=(await c.execute(select(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.to_team_id).with_for_update())).first()
        from_team=(await c.execute(select(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.from_team_id).with_for_update())).first()
        if not to_team or not from_team:
            await c.execute(update(trades).where(trades.c.id==trade_id).values(status="cancelled")); return False
        if tr.cash_amount and to_team.purse<tr.cash_amount:
            await c.execute(update(trades).where(trades.c.id==trade_id).values(status="cancelled")); return False
        await c.execute(update(squad).where(squad.c.guild_id==tr.guild_id,squad.c.team_id==tr.from_team_id,squad.c.player_id==tr.send_player_id).values(team_id=tr.to_team_id))
        if receiver:
            await c.execute(update(squad).where(squad.c.guild_id==tr.guild_id,squad.c.team_id==tr.to_team_id,squad.c.player_id==tr.receive_player_id).values(team_id=tr.from_team_id))
        if tr.cash_amount:
            await c.execute(update(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.to_team_id).values(purse=teams.c.purse-tr.cash_amount))
            await c.execute(update(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.from_team_id).values(purse=teams.c.purse+tr.cash_amount))
            from database import transactions
            await c.execute(insert(transactions).values(guild_id=tr.guild_id,team_id=tr.to_team_id,amount=-tr.cash_amount,reason="Player trade payment",reference_id=trade_id))
            await c.execute(insert(transactions).values(guild_id=tr.guild_id,team_id=tr.from_team_id,amount=tr.cash_amount,reason="Player trade receipt",reference_id=trade_id))
        await c.execute(update(trades).where(trades.c.id==trade_id).values(status="completed"))
    return True

class TradeView(discord.ui.View):
    def __init__(self,bot,trade_id): super().__init__(timeout=None); self.bot=bot; self.trade_id=trade_id
    @discord.ui.button(label="APPROVE",style=discord.ButtonStyle.success,custom_id="ipl:v3:trade:approve")
    async def approve(self,interaction,button): await self.act(interaction,True)
    @discord.ui.button(label="REJECT",style=discord.ButtonStyle.danger,custom_id="ipl:v3:trade:reject")
    async def reject(self,interaction,button): await self.act(interaction,False)
    async def act(self,interaction,approve):
        async with self.bot.db.engine.begin() as c:
            if not interaction.guild: return await interaction.response.send_message("Use this in the trade's server.",ephemeral=True)
            if interaction.user.id==interaction.guild.owner_id and await is_blacklisted(self.bot.db,interaction.user.id): return await interaction.response.send_message("Your account is globally blacklisted from this bot in servers you own.",ephemeral=True)
            tr=(await c.execute(select(trades).where(trades.c.id==self.trade_id,trades.c.guild_id==interaction.guild.id).with_for_update())).first()
            if not tr or tr.status!="pending": return await interaction.response.send_message("This trade is no longer pending.",ephemeral=True)
            from_team=(await c.execute(select(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.from_team_id))).first()
            to_team=(await c.execute(select(teams).where(teams.c.guild_id==tr.guild_id,teams.c.id==tr.to_team_id))).first()
            if not from_team or not to_team: return await interaction.response.send_message("A team no longer exists; trade cannot proceed.",ephemeral=True)
            # The exact team owner account or a member carrying the configured team-owner role may approve.
            from_ok=bool(from_team.owner_user_id==interaction.user.id or (from_team.owner_role_id and any(r.id==from_team.owner_role_id for r in getattr(interaction.user,"roles",[]))))
            to_ok=bool(to_team.owner_user_id==interaction.user.id or (to_team.owner_role_id and any(r.id==to_team.owner_role_id for r in getattr(interaction.user,"roles",[]))))
            if not (from_ok or to_ok): return await interaction.response.send_message("Only the two team owners can decide this trade.",ephemeral=True)
            if from_ok and to_ok: return await interaction.response.send_message("One Discord account cannot approve both sides.",ephemeral=True)
            if not approve:
                await c.execute(update(trades).where(trades.c.guild_id==interaction.guild.id,trades.c.id==self.trade_id).values(status="rejected"))
                return await interaction.response.send_message("Trade rejected and cancelled.")
            col=trades.c.from_approved if from_ok else trades.c.to_approved
            await c.execute(update(trades).where(trades.c.guild_id==interaction.guild.id,trades.c.id==self.trade_id).values({col.key:True}))
        done=await process_trade(self.bot,self.trade_id)
        await interaction.response.send_message("Both sides approved; trade completed." if done else "Approval recorded. Waiting for the other owner or trade validation.",ephemeral=True)
        if done and interaction.channel:
            left=f"<@{from_team.owner_user_id}>" if from_team.owner_user_id else (f"<@&{from_team.owner_role_id}>" if from_team.owner_role_id else from_team.name)
            right=f"<@{to_team.owner_user_id}>" if to_team.owner_user_id else (f"<@&{to_team.owner_role_id}>" if to_team.owner_role_id else to_team.name)
            await interaction.channel.send(f"{left} {right} — trade **#{self.trade_id}** has been completed.")

class Trades(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="trade",description="Propose a player trade")
    async def trade(self,ctx,player_name:str,destination_team_id:int,receive_player_name:str|None=None):
        if not ctx.guild: return await ctx.reply("Use this in a server.")
        sender=await team_for_member(self.bot.db,ctx.guild.id,ctx.author)
        if not sender: return await ctx.reply("You are not assigned as a team owner.")
        async with self.bot.db.engine.begin() as c:
            dest=(await c.execute(select(teams).where(teams.c.guild_id==ctx.guild.id,teams.c.id==destination_team_id))).first()
            if not dest or dest.id==sender["id"]: return await ctx.reply("Invalid destination team.")
            if not (dest.owner_user_id or dest.owner_role_id): return await ctx.reply("Destination team has no assigned owner or owner role yet.")
            p=(await c.execute(select(players).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(player_name)))).first()
            if not p: return await ctx.reply("Player not found.")
            owned=(await c.execute(select(squad).where(squad.c.guild_id==ctx.guild.id,squad.c.team_id==sender["id"],squad.c.player_id==p.id))).first()
            if not owned: return await ctx.reply("Your team does not own that player.")
            receive_id=None; cash=owned.purchase_price
            if receive_player_name:
                rp=(await c.execute(select(players).where(players.c.guild_id==ctx.guild.id,players.c.name.ilike(receive_player_name)))).first()
                if not rp: return await ctx.reply("Requested incoming player not found.")
                if rp.id==p.id: return await ctx.reply("A player cannot be traded for themselves.")
                incoming=(await c.execute(select(squad).where(squad.c.guild_id==ctx.guild.id,squad.c.team_id==dest.id,squad.c.player_id==rp.id))).first()
                if not incoming: return await ctx.reply("Destination team does not own the requested incoming player.")
                receive_id=rp.id; cash=0
            tid=(await c.execute(insert(trades).values(guild_id=ctx.guild.id,from_team_id=sender["id"],to_team_id=dest.id,send_player_id=p.id,receive_player_id=receive_id,cash_amount=cash).returning(trades.c.id))).scalar_one()
        view=TradeView(self.bot,tid)
        msg=await ctx.reply(f"Trade request **#{tid}**: {sender['name']} sends **{p.name}** to **{dest.name}**"+(f" for **{receive_player_name}**." if receive_player_name else f" for purse payment **{cash:,}**."),view=view)
        async with self.bot.db.engine.begin() as c: await c.execute(update(trades).where(trades.c.id==tid).values(message_id=msg.id))
        self.bot.add_view(view,message_id=msg.id)
    @commands.hybrid_command(name="tradestatus")
    async def tradestatus(self,ctx,trade_id:int|None=None):
        if not ctx.guild: return await ctx.reply("Use this in a server.")
        async with self.bot.db.sessions() as s:
            q=select(trades).where(trades.c.guild_id==ctx.guild.id)
            if trade_id: q=q.where(trades.c.id==trade_id)
            rows=(await s.execute(q.order_by(trades.c.id.desc()).limit(15))).all()
        if not rows: return await ctx.reply("No trade records found.")
        await ctx.reply("\n".join(f"#{r.id}: {r.status} — team {r.from_team_id} → team {r.to_team_id} (approvals {r.from_approved}/{r.to_approved})" for r in rows))

async def setup(bot): await bot.add_cog(Trades(bot))
