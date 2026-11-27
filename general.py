import discord
from discord.ext import commands
from sqlalchemy import select, insert
from database import requests, players, teams
from services.core import is_manager

HELP_DATA={
"General":[("help","Show commands","Everyone","c!help"),("request","Send a private request to bot admin","Everyone","c!request Bug report | Details") ,("teams","List server teams","Everyone","c!teams"),("players","Search player catalogue","Everyone","c!players kohli"),("squad","Search a team squad","Everyone","c!squad roy" )],
"Auction":[(x,"Auction control","Management",f"c!{x} 1") for x in ["startauction","endauction","breakauction","resumeauction","nextplayer","sold","unsold","reauctionunsold","unsoldplayers","auctionreport","setbidincrement"]],
"Team management":[(x,"Manage teams, owners and squads","Management",f"c!{x}") for x in ["addteam","removeteam","setpurse","addpurse","subtractpurse","addplayer","removeplayer","setteamrole","setteamowner","setplayercategory"]],
"Player pools":[(x,"Manage player pools","Management",f"c!{x}") for x in ["createpool","deletepool","addpoolplayer","removepoolplayer","viewpool","selectpool","importplayers"]],
"Trades":[("trade","Propose a player trade","Team owner","c!trade Player 2 [incoming player]"),("tradestatus","Check trade records","Everyone","c!tradestatus")],
"Wallet":[("wallet / balance","View available purse","Team owner; management can specify team ID","c!wallet [team_id]")],
"Server configuration":[("setmanagerrole","Set management role","Server owner","c!setmanagerrole @Role"),("setauctionchannel","Set auction/report channel","Server owner","c!setauctionchannel #auction"),("setprefix","Change command prefix","Server owner","c!setprefix !")],
"Bot administrator":[(x,"Global bot administration","BOT_ADMIN_ID only",f"c!{x}") for x in ["blacklist","unblacklist","servercount","botstatus"]]
}

class General(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="help")
    async def help_cmd(self,ctx):
        manager=bool(ctx.guild and await is_manager(self.bot.db,ctx.guild,ctx.author))
        admin=ctx.author.id==self.bot.config.bot_admin_id
        owner=bool(ctx.guild and ctx.guild.owner_id==ctx.author.id)
        team_owner=False
        if ctx.guild:
            from services.core import team_for_member
            team_owner=bool(await team_for_member(self.bot.db,ctx.guild.id,ctx.author))
        e=discord.Embed(title="IPL Auction Bot V3 — Help",description="Prefix commands also have slash-command equivalents where applicable.",color=discord.Color.blurple())
        for cat,items in HELP_DATA.items():
            visible=[]
            for name,desc,perm,usage in items:
                allowed=(perm=="Everyone" or (perm=="Management" and manager) or (perm=="Team owner" and team_owner) or (perm=="Server owner" and owner) or (perm=="BOT_ADMIN_ID only" and admin) or "management can specify" in perm and (manager or team_owner))
                if allowed: visible.append(f"`{name}` — {desc}\nPermission: {perm}\nExample: `{usage}`")
            if visible: e.add_field(name=cat,value="\n\n".join(visible)[:1024],inline=False)
        await ctx.reply(embed=e)
    @commands.hybrid_command(name="request")
    async def request(self,ctx,category:str,*,description:str):
        if category.lower() not in {"bug report","feature request","help request","other"}:
            return await ctx.reply("Category must be: Bug report, Feature request, Help request, or Other.")
        if " | " in description: description,extra=description.split(" | ",1)
        else: extra=None
        async with self.bot.db.engine.begin() as c:
            await c.execute(insert(requests).values(guild_id=ctx.guild.id if ctx.guild else None,user_id=ctx.author.id,category=category.title(),description=description,extra=extra))
        try:
            admin=await self.bot.fetch_user(self.bot.config.bot_admin_id)
            e=discord.Embed(title="New bot request",color=discord.Color.orange())
            e.add_field(name="Requester",value=f"{ctx.author} ({ctx.author.id})",inline=False)
            e.add_field(name="Server",value=f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "Direct message",inline=False)
            e.add_field(name="Category",value=category.title(),inline=True); e.add_field(name="Description",value=description[:1024],inline=False)
            if extra: e.add_field(name="Additional information",value=extra[:1024],inline=False)
            e.timestamp=discord.utils.utcnow(); await admin.send(embed=e)
        except (discord.Forbidden,discord.HTTPException,AttributeError):
            return await ctx.reply("Your request was saved, but the bot administrator's DMs could not be delivered. Please contact them another way.")
        await ctx.reply("Your request was sent to the bot administrator.")
    @commands.hybrid_command(name="players")
    async def players_cmd(self,ctx,*,query:str=""):
        if not ctx.guild: return await ctx.reply("Use this command in a server.")
        async with self.bot.db.sessions() as s:
            q=select(players).where(players.c.guild_id==ctx.guild.id)
            if query: q=q.where(players.c.name.ilike(f"%{query}%"))
            rows=(await s.execute(q.order_by(players.c.name).limit(30))).all()
        await ctx.reply("\n".join(f"• {r.name} — {r.category}" for r in rows) or "No matching players.")

async def setup(bot): await bot.add_cog(General(bot))
