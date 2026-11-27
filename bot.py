import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DB_PATH", "auction.db")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            purse REAL NOT NULL DEFAULT 100.0,
            PRIMARY KEY (guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Unknown',
            status TEXT NOT NULL DEFAULT 'pending',
            current_bid REAL NOT NULL DEFAULT 0,
            current_team TEXT,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            active_player_id INTEGER
        );
        """)

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_guild

async def admin_check(interaction: discord.Interaction) -> bool:
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You need the **Manage Server** permission to run this command.",
            ephemeral=True
        )
        return False
    return True

def get_active(guild_id):
    with db() as con:
        setting = con.execute(
            "SELECT active_player_id FROM settings WHERE guild_id=?", (guild_id,)
        ).fetchone()
        if not setting or setting["active_player_id"] is None:
            return None
        return con.execute(
            "SELECT * FROM players WHERE id=? AND guild_id=?",
            (setting["active_player_id"], guild_id)
        ).fetchone()

@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}; synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Command sync failed: {exc}")

@bot.tree.command(name="setup", description="Create the auction teams and set each team's purse.")
@app_commands.describe(teams="Comma-separated team names", purse="Starting purse for each team")
async def setup(interaction: discord.Interaction, teams: str, purse: app_commands.Range[float, 1, 100000] = 100.0):
    if not await admin_check(interaction):
        return
    names = [x.strip() for x in teams.split(",") if x.strip()]
    if not names or len(names) > 20:
        await interaction.response.send_message("Enter between 1 and 20 comma-separated team names.", ephemeral=True)
        return
    with db() as con:
        con.execute("DELETE FROM teams WHERE guild_id=?", (interaction.guild_id,))
        con.executemany(
            "INSERT INTO teams(guild_id,name,purse) VALUES(?,?,?)",
            [(interaction.guild_id, name, float(purse)) for name in names]
        )
    await interaction.response.send_message(
        f"Auction setup complete. Teams: {', '.join(names)}. Starting purse: **{purse:g} Cr** each."
    )

@bot.tree.command(name="addplayer", description="Add a player to the auction queue.")
@app_commands.describe(name="Player name", role="Player role (BAT, BOWL, AR, WK)", base_price="Base price in crore")
async def addplayer(interaction: discord.Interaction, name: str, role: str = "Unknown",
                    base_price: app_commands.Range[float, 0.05, 1000] = 0.2):
    if not await admin_check(interaction):
        return
    with db() as con:
        cur = con.execute(
            """INSERT INTO players(guild_id,name,role,status,current_bid,added_at)
               VALUES(?,?,?,'pending',?,?)""",
            (interaction.guild_id, name.strip(), role.upper(), float(base_price),
             datetime.now(timezone.utc).isoformat())
        )
        player_id = cur.lastrowid
    await interaction.response.send_message(
        f"Added **{name}** ({role.upper()}) to the queue at **₹{base_price:g} Cr**. Player ID: `{player_id}`"
    )

@bot.tree.command(name="next", description="Put the next queued player up for auction.")
async def next_player(interaction: discord.Interaction):
    if not await admin_check(interaction):
        return
    with db() as con:
        current = get_active(interaction.guild_id)
        if current and current["status"] == "live":
            await interaction.response.send_message(
                "Finish the current player with `/sold` or `/unsold` first.", ephemeral=True
            )
            return
        player = con.execute(
            "SELECT * FROM players WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 1",
            (interaction.guild_id,)
        ).fetchone()
        if not player:
            await interaction.response.send_message("The player queue is empty.")
            return
        con.execute("UPDATE players SET status='live' WHERE id=?", (player["id"],))
        con.execute(
            """INSERT INTO settings(guild_id,active_player_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET active_player_id=excluded.active_player_id""",
            (interaction.guild_id, player["id"])
        )
    await interaction.response.send_message(
        f"🔨 **NOW UP FOR AUCTION: {player['name']}**\n"
        f"Role: **{player['role']}** • Opening bid: **₹{player['current_bid']:g} Cr**\n"
        "Use `/bid` to place a bid."
    )

@bot.tree.command(name="bid", description="Place a bid for the player currently up for auction.")
@app_commands.describe(team="Your franchise name", amount="Your new total bid in crore")
async def bid(interaction: discord.Interaction, team: str, amount: app_commands.Range[float, 0.05, 1000]):
    player = get_active(interaction.guild_id)
    if not player or player["status"] != "live":
        await interaction.response.send_message("There is no player currently up for auction.", ephemeral=True)
        return
    with db() as con:
        team_row = con.execute(
            "SELECT * FROM teams WHERE guild_id=? AND name=? COLLATE NOCASE",
            (interaction.guild_id, team.strip())
        ).fetchone()
        if not team_row:
            await interaction.response.send_message("That team is not registered. Check `/teams`.", ephemeral=True)
            return
        if amount <= player["current_bid"]:
            await interaction.response.send_message(
                f"Bid must be higher than the current bid of ₹{player['current_bid']:g} Cr.", ephemeral=True
            )
            return
        if amount > team_row["purse"]:
            await interaction.response.send_message(
                f"{team_row['name']} only has ₹{team_row['purse']:g} Cr remaining.", ephemeral=True
            )
            return
        con.execute(
            "UPDATE players SET current_bid=?, current_team=? WHERE id=?",
            (float(amount), team_row["name"], player["id"])
        )
    await interaction.response.send_message(
        f"💰 **{team_row['name']}** bids **₹{amount:g} Cr** for **{player['name']}**!"
    )

@bot.tree.command(name="sold", description="Sell the current player to the highest-bidding team.")
async def sold(interaction: discord.Interaction):
    if not await admin_check(interaction):
        return
    player = get_active(interaction.guild_id)
    if not player or player["status"] != "live":
        await interaction.response.send_message("No live player to sell.", ephemeral=True)
        return
    if not player["current_team"]:
        await interaction.response.send_message("No bids have been placed. Use `/unsold` instead.", ephemeral=True)
        return
    with db() as con:
        team = con.execute(
            "SELECT * FROM teams WHERE guild_id=? AND name=?",
            (interaction.guild_id, player["current_team"])
        ).fetchone()
        if not team or player["current_bid"] > team["purse"]:
            await interaction.response.send_message("Team purse changed; cannot complete this sale.", ephemeral=True)
            return
        con.execute(
            "UPDATE teams SET purse=purse-? WHERE guild_id=? AND name=?",
            (player["current_bid"], interaction.guild_id, player["current_team"])
        )
        con.execute(
            "INSERT INTO purchases(guild_id,player_id,team_name,price) VALUES(?,?,?,?)",
            (interaction.guild_id, player["id"], player["current_team"], player["current_bid"])
        )
        con.execute("UPDATE players SET status='sold' WHERE id=?", (player["id"],))
        con.execute("UPDATE settings SET active_player_id=NULL WHERE guild_id=?", (interaction.guild_id,))
    await interaction.response.send_message(
        f"🔨 **SOLD!** {player['name']} → **{player['current_team']}** for **₹{player['current_bid']:g} Cr**."
    )

@bot.tree.command(name="unsold", description="Mark the current player as unsold.")
async def unsold(interaction: discord.Interaction):
    if not await admin_check(interaction):
        return
    player = get_active(interaction.guild_id)
    if not player or player["status"] != "live":
        await interaction.response.send_message("No live player to mark unsold.", ephemeral=True)
        return
    with db() as con:
        con.execute("UPDATE players SET status='unsold' WHERE id=?", (player["id"],))
        con.execute("UPDATE settings SET active_player_id=NULL WHERE guild_id=?", (interaction.guild_id,))
    await interaction.response.send_message(f"❌ **UNSOLD:** {player['name']}")

@bot.tree.command(name="teams", description="Show every team's remaining purse.")
async def teams(interaction: discord.Interaction):
    with db() as con:
        rows = con.execute(
            "SELECT name,purse FROM teams WHERE guild_id=? ORDER BY name",
            (interaction.guild_id,)
        ).fetchall()
    if not rows:
        await interaction.response.send_message("No teams yet. An admin can run `/setup`.")
        return
    lines = [f"**{r['name']}** — ₹{r['purse']:g} Cr" for r in rows]
    await interaction.response.send_message("**Team purses**\n" + "\n".join(lines))

@bot.tree.command(name="squad", description="Show players bought by a franchise.")
@app_commands.describe(team="Franchise name")
async def squad(interaction: discord.Interaction, team: str):
    with db() as con:
        team_row = con.execute(
            "SELECT * FROM teams WHERE guild_id=? AND name=? COLLATE NOCASE",
            (interaction.guild_id, team.strip())
        ).fetchone()
        if not team_row:
            await interaction.response.send_message("Team not found. Check `/teams`.", ephemeral=True)
            return
        rows = con.execute(
            """SELECT p.name,p.role,pu.price FROM purchases pu
               JOIN players p ON p.id=pu.player_id
               WHERE pu.guild_id=? AND pu.team_name=? ORDER BY pu.id""",
            (interaction.guild_id, team_row["name"])
        ).fetchall()
    if not rows:
        body = "No players bought yet."
    else:
        body = "\n".join(f"• {r['name']} ({r['role']}) — ₹{r['price']:g} Cr" for r in rows)
    await interaction.response.send_message(
        f"**{team_row['name']} squad**\n{body}\n\nRemaining purse: **₹{team_row['purse']:g} Cr**"
    )

@bot.tree.command(name="queue", description="Show the next players in the auction queue.")
async def queue(interaction: discord.Interaction):
    with db() as con:
        rows = con.execute(
            "SELECT name,role,current_bid FROM players WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 15",
            (interaction.guild_id,)
        ).fetchall()
    if not rows:
        await interaction.response.send_message("The queue is empty.")
        return
    lines = [f"{i}. **{r['name']}** ({r['role']}) — base ₹{r['current_bid']:g} Cr"
             for i, r in enumerate(rows, 1)]
    await interaction.response.send_message("**Upcoming players**\n" + "\n".join(lines))

@bot.tree.command(name="auctionstatus", description="Show the player currently being auctioned.")
async def auctionstatus(interaction: discord.Interaction):
    player = get_active(interaction.guild_id)
    if not player or player["status"] != "live":
        await interaction.response.send_message("No player is currently up for auction.")
        return
    bidder = player["current_team"] or "No bids yet"
    await interaction.response.send_message(
        f"**{player['name']}** ({player['role']})\nCurrent bid: **₹{player['current_bid']:g} Cr**\nHighest bidder: **{bidder}**"
    )

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")

bot.run(TOKEN)

