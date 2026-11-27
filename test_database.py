"""Synchronous schema/isolation tests; runtime uses SQLAlchemy's async driver."""
from sqlalchemy import create_engine, insert, select, update
from database import metadata, guilds, teams


def make_db():
    engine=create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def test_guild_data_isolation():
    engine=make_db()
    with engine.begin() as c:
        c.execute(insert(guilds),[{"guild_id":1,"prefix":"c!"},{"guild_id":2,"prefix":"c!"}])
        c.execute(insert(teams),[{"guild_id":1,"name":"One","purse":100},{"guild_id":2,"name":"Two","purse":200}])
        a=c.execute(select(teams).where(teams.c.guild_id==1)).all()
        b=c.execute(select(teams).where(teams.c.guild_id==2)).all()
    assert [x.name for x in a]==["One"]
    assert [x.name for x in b]==["Two"]
    engine.dispose()


def test_prefix_is_per_guild():
    engine=make_db()
    with engine.begin() as c:
        c.execute(insert(guilds),[{"guild_id":1,"prefix":"c!"},{"guild_id":2,"prefix":"c!"}])
        c.execute(update(guilds).where(guilds.c.guild_id==1).values(prefix="!"))
        prefixes=dict(c.execute(select(guilds.c.guild_id,guilds.c.prefix)).all())
    assert prefixes=={1:"!",2:"c!"}
    engine.dispose()
