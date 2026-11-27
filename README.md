IPL Discord Auction Bot (Starter)
A beginner-friendly Discord slash-command bot for running a mock IPL-style player auction.
Features in this starter version
Set up franchises and a starting purse
Add players to a queue
Bring the next player up for auction
Let users bid for a franchise
Sell a player to the highest bidder or mark them unsold
View team purses, squads and the upcoming queue
Stores auction data in SQLite, so it survives bot restarts
All prices are in crore (Cr). The default purse is ₹100 Cr per team; change it with /setup.
1. Create your Discord bot
Open https://discord.com/developers/applications.
Select New Application, give it a name, then open Bot.
Create/reset the bot token and copy it. Keep it private; never post it on GitHub.
Under OAuth2 → URL Generator, select scopes bot and applications.commands.
Select the bot permission Send Messages. Open the generated invite URL and add the bot to your test server.
You do not need Message Content Intent for this slash-command bot.
2. Run it locally
You need Python 3.10 or newer.
python -m venv .venv
Activate the environment:
Windows: .venv\\Scripts\\activate
macOS/Linux: source .venv/bin/activate
Install dependencies:
pip install -r requirements.txt
Copy .env.example to a new file named .env, then paste your bot token after DISCORD_TOKEN=.
Start the bot:
python bot.py
When it says it logged in and synced commands, try /setup in your Discord server.
3. Suggested first test
Run these commands in your server:
/setup teams: RCB, CSK, MI, KKR purse: 100
/addplayer name: Virat Kohli role: BAT base_price: 2
/addplayer name: Jasprit Bumrah role: BOWL base_price: 2
/next
/bid team: RCB amount: 5
/bid team: MI amount: 5.5
/sold
/teams
/squad team: MI
The /setup, /addplayer, /next, /sold, and /unsold commands require the Discord Manage Server permission. Bidding is open to server members.
Commands
Command
Purpose
/setup
Create teams and set purse
/addplayer
Add a player to the queue
/next
Start the next player
/bid
Place a bid for a team
/sold
Sell to the current highest bidder
/unsold
Mark current player unsold
/teams
Show remaining purses
/squad
Show a team's purchased players
/queue
Show upcoming players
/auctionstatus
Show the live player and current bid
Important notes
This is an MVP starter, not a production auction platform.
The auctioneer/admin manually decides when bidding ends by using /sold.
It currently supports one active auction player per Discord server.
Team names in /bid and /squad must match the names created in /setup.
Do not commit .env or your bot token to GitHub. Add .env to .gitignore.
Roadmap ideas
Timed bidding and automatic countdown
Nomination order and player categories
Retentions and Right to Match (RTM)
Squad size and overseas-player limits
Bid increment rules
   Auction history and downloadable results
Buttons and live auction embeds
