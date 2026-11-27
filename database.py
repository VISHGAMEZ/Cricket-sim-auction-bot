from sqlalchemy import (MetaData, Table, Column, Integer, String, Boolean, BigInteger,
                        ForeignKey, Text, DateTime, UniqueConstraint, Index, text)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.sql import func
from sqlalchemy import event
from datetime import datetime, timezone

metadata = MetaData()
guilds = Table("guilds", metadata,
    Column("guild_id", BigInteger, primary_key=True), Column("prefix", String(12), nullable=False, default="c!"),
    Column("manager_role_id", BigInteger), Column("auction_channel_id", BigInteger), Column("active_pool_id", Integer),
    Column("bid_increment", Integer, nullable=False, default=1), Column("created_at", DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)))
teams = Table("teams", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("guild_id", BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"), nullable=False),
    Column("name", String(100), nullable=False), Column("aliases", Text, nullable=False, default=""), Column("purse", Integer, nullable=False, default=10000),
    Column("owner_role_id", BigInteger), Column("owner_user_id", BigInteger), UniqueConstraint("guild_id", "name"))
players = Table("players", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("name", String(120), nullable=False),
    Column("category", String(80), nullable=False, default="Unspecified"), UniqueConstraint("guild_id", "name"))
pools = Table("pools", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("name", String(80), nullable=False),
    UniqueConstraint("guild_id", "name"))
pool_players = Table("pool_players", metadata,
    Column("pool_id", Integer, ForeignKey("pools.id", ondelete="CASCADE"), primary_key=True),
    Column("player_id", Integer, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True), Column("position", Integer, nullable=False, default=0))
tournaments = Table("tournaments", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("name", String(120), nullable=False),
    Column("pool_id", Integer), Column("status", String(20), nullable=False, default="active"), Column("channel_id", BigInteger),
    Column("current_player_id", Integer), Column("round_no", Integer, nullable=False, default=1), Column("highest_bid", Integer, nullable=False, default=0),
    Column("highest_team_id", Integer), Column("message_id", BigInteger), Column("report", Text), Column("created_at", DateTime, nullable=False, default=datetime.utcnow))
bids = Table("bids", metadata,
    Column("id", Integer, primary_key=True), Column("tournament_id", Integer, nullable=False), Column("guild_id", BigInteger, nullable=False),
    Column("player_id", Integer, nullable=False), Column("round_no", Integer, nullable=False), Column("team_id", Integer, nullable=False),
    Column("amount", Integer, nullable=False), Column("bidder_id", BigInteger, nullable=False), Column("created_at", DateTime, nullable=False, default=datetime.utcnow))
passes = Table("passes", metadata,
    Column("tournament_id", Integer, primary_key=True), Column("player_id", Integer, primary_key=True), Column("round_no", Integer, primary_key=True),
    Column("team_id", Integer, primary_key=True), Column("user_id", BigInteger, nullable=False))
auction_results = Table("auction_results", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("tournament_id", Integer, nullable=False),
    Column("player_id", Integer, nullable=False), Column("round_no", Integer, nullable=False), Column("result", String(12), nullable=False),
    Column("team_id", Integer), Column("price", Integer, nullable=False, default=0), Column("original_price", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow), UniqueConstraint("tournament_id", "player_id", "round_no"))
squad = Table("squad", metadata,
    Column("guild_id", BigInteger, primary_key=True), Column("team_id", Integer, primary_key=True), Column("player_id", Integer, primary_key=True),
    Column("purchase_price", Integer, nullable=False, default=0), Column("tournament_id", Integer))
transactions = Table("wallet_transactions", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("team_id", Integer, nullable=False),
    Column("amount", Integer, nullable=False), Column("reason", String(200), nullable=False), Column("reference_id", Integer),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow))
trades = Table("trades", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger, nullable=False), Column("from_team_id", Integer, nullable=False),
    Column("to_team_id", Integer, nullable=False), Column("send_player_id", Integer, nullable=False), Column("receive_player_id", Integer),
    Column("cash_amount", Integer, nullable=False, default=0), Column("from_approved", Boolean, nullable=False, default=False),
    Column("to_approved", Boolean, nullable=False, default=False), Column("status", String(20), nullable=False, default="pending"),
    Column("message_id", BigInteger), Column("created_at", DateTime, nullable=False, default=datetime.utcnow))
unsold_queue = Table("unsold_queue", metadata,
    Column("tournament_id", Integer, primary_key=True), Column("player_id", Integer, primary_key=True),
    Column("position", Integer, nullable=False, default=0))
blacklist = Table("blacklist", metadata, Column("owner_user_id", BigInteger, primary_key=True), Column("reason", String(250), nullable=False, default=""), Column("created_at", DateTime, nullable=False, default=datetime.utcnow))
requests = Table("user_requests", metadata,
    Column("id", Integer, primary_key=True), Column("guild_id", BigInteger), Column("user_id", BigInteger, nullable=False),
    Column("category", String(40), nullable=False), Column("description", Text, nullable=False), Column("extra", Text),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow))

class Database:
    def __init__(self, url):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_foreign_keys(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor(); cursor.execute("PRAGMA foreign_keys=ON"); cursor.close()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
    async def init(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
    async def close(self): await self.engine.dispose()
    async def ensure_guild(self, guild_id, prefix="c!"):
        async with self.engine.begin() as c:
            await c.execute(guilds.insert().values(guild_id=guild_id, prefix=prefix).prefix_with("OR IGNORE") if self.engine.url.get_backend_name()=="sqlite" else text("INSERT INTO guilds (guild_id,prefix) VALUES (:g,:p) ON CONFLICT (guild_id) DO NOTHING").bindparams(g=guild_id,p=prefix))
    async def get_prefix(self, guild_id, default="c!"):
        async with self.sessions() as s:
            r=await s.execute(guilds.select().where(guilds.c.guild_id==guild_id)); row=r.first()
            return row.prefix if row else default
