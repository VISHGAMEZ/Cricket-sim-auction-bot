from sqlalchemy import select, insert, update, delete, func
from database import (guilds, teams, players, pools, pool_players, tournaments, bids, passes,
                      auction_results, squad, transactions, trades, blacklist, unsold_queue)

class DomainError(Exception): pass

async def is_blacklisted(db, owner_id):
    async with db.sessions() as s: return (await s.execute(select(blacklist).where(blacklist.c.owner_user_id==owner_id))).first() is not None

async def is_manager(db, guild, member):
    if guild.owner_id == member.id: return True
    async with db.sessions() as s:
        row=(await s.execute(select(guilds.c.manager_role_id).where(guilds.c.guild_id==guild.id))).first()
    return bool(row and row.manager_role_id and any(r.id==row.manager_role_id for r in getattr(member,"roles",[])))

async def team_for_member(db, guild_id, member):
    async with db.sessions() as s:
        rows=(await s.execute(select(teams).where(teams.c.guild_id==guild_id))).all()
    matches=[]
    for r in rows:
        t=r._mapping
        if t["owner_user_id"]==member.id or (t["owner_role_id"] and any(x.id==t["owner_role_id"] for x in getattr(member,"roles",[]))): matches.append(t)
    return matches[0] if len(matches)==1 else None

async def get_team(db, guild_id, team_id):
    async with db.sessions() as s: return (await s.execute(select(teams).where(teams.c.guild_id==guild_id,teams.c.id==team_id))).first()

async def settle_player(db, auction, result, team_id=None, price=0):
    """Atomic result, squad and purse update. Idempotency is enforced by unique result key."""
    async with db.engine.begin() as c:
        locked=(await c.execute(select(tournaments).where(tournaments.c.id==auction.id,tournaments.c.guild_id==auction.guild_id).with_for_update())).first()
        if not locked or locked.current_player_id!=auction.current_player_id or locked.round_no!=auction.round_no: return False
        existing=await c.execute(select(auction_results).where(auction_results.c.tournament_id==auction.id,
            auction_results.c.player_id==auction.current_player_id,auction_results.c.round_no==auction.round_no))
        if existing.first(): return False
        if result=="SOLD":
            if not team_id or price<=0: raise DomainError("A player can only be marked sold when there is a valid winning bid.")
            team=(await c.execute(select(teams).where(teams.c.guild_id==auction.guild_id,teams.c.id==team_id).with_for_update())).first()
            if not team or team.purse < price: raise DomainError("Winning team no longer has enough purse.")
            await c.execute(update(teams).where(teams.c.id==team_id,teams.c.guild_id==auction.guild_id).values(purse=teams.c.purse-price))
            await c.execute(insert(squad).values(guild_id=auction.guild_id,team_id=team_id,player_id=auction.current_player_id,purchase_price=price,tournament_id=auction.id))
            await c.execute(insert(transactions).values(guild_id=auction.guild_id,team_id=team_id,amount=-price,reason="Auction purchase",reference_id=auction.id))
        await c.execute(insert(auction_results).values(guild_id=auction.guild_id,tournament_id=auction.id,player_id=auction.current_player_id,
            round_no=auction.round_no,result=result,team_id=team_id,price=price,original_price=price))
        if result=="UNSOLD":
            pos=(await c.execute(select(func.count()).select_from(unsold_queue).where(unsold_queue.c.tournament_id==auction.id))).scalar_one()
            await c.execute(insert(unsold_queue).values(tournament_id=auction.id,player_id=auction.current_player_id,position=pos))
        await c.execute(update(tournaments).where(tournaments.c.id==auction.id).values(current_player_id=None,highest_bid=0,highest_team_id=None))
    return True

async def start_next_player(db, tournament_id):
    async with db.engine.begin() as c:
        t=(await c.execute(select(tournaments).where(tournaments.c.id==tournament_id).with_for_update())).first()
        if not t or t.status!="active" or t.current_player_id is not None: return None
        used=select(auction_results.c.player_id).where(auction_results.c.tournament_id==t.id)
        row=(await c.execute(select(pool_players.c.player_id).where(pool_players.c.pool_id==t.pool_id,
             pool_players.c.player_id.not_in(used)).order_by(pool_players.c.position,pool_players.c.player_id).limit(1))).first()
        if not row: return None
        await c.execute(update(tournaments).where(tournaments.c.id==t.id).values(current_player_id=row.player_id,highest_bid=0,highest_team_id=None))
        return row.player_id

async def start_next_unsold(db, tournament_id):
    """Explicit management-triggered reauction; each reauction gets a fresh pass round."""
    async with db.engine.begin() as c:
        t=(await c.execute(select(tournaments).where(tournaments.c.id==tournament_id).with_for_update())).first()
        if not t or t.status!="active" or t.current_player_id is not None: return None
        row=(await c.execute(select(unsold_queue).where(unsold_queue.c.tournament_id==t.id).order_by(unsold_queue.c.position).limit(1).with_for_update())).first()
        if not row: return None
        await c.execute(delete(unsold_queue).where(unsold_queue.c.tournament_id==t.id,unsold_queue.c.player_id==row.player_id))
        new_round=t.round_no+1
        await c.execute(update(tournaments).where(tournaments.c.id==t.id).values(current_player_id=row.player_id,round_no=new_round,highest_bid=0,highest_team_id=None))
        return row.player_id

async def finalize_if_all_passed(db, tournament_id):
    async with db.sessions() as s:
        t=(await s.execute(select(tournaments).where(tournaments.c.id==tournament_id))).first()
        if not t or t.status!="active" or not t.current_player_id: return False
        team_rows=(await s.execute(select(teams.c.id).where(teams.c.guild_id==t.guild_id))).all()
        passed=(await s.execute(select(passes.c.team_id).where(passes.c.tournament_id==t.id,passes.c.player_id==t.current_player_id,passes.c.round_no==t.round_no))).all()
    passed_ids={x[0] for x in passed}
    if t.highest_team_id: passed_ids.add(t.highest_team_id)
    if len(passed_ids) < len(team_rows): return False
    done=await settle_player(db,t,"SOLD" if t.highest_team_id else "UNSOLD",t.highest_team_id,t.highest_bid)
    if not done: return False
    await start_next_player(db,t.id)
    return True
