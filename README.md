# OddSea: NFL + European soccer pricing, injuries, live odds and a pick tracker

(The code inside is still the `betmodel` package, so commands like `.\betmodel.cmd ...` and
`python -m betmodel ...` are unchanged.)

Prices NFL games and soccer matches (Premier League, La Liga, Serie A, Champions League, Europa
League, plus 20 other European leagues that link the ratings). It adjusts for injuries and
lineups, compares fair prices with DraftKings/FanDuel, logs every recommended pick in a local
database, and grades them against final scores and the sharp closing line.

## Quick start (Windows)

1. Install Python from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"**.
2. Double-click **`app.bat`**. It opens the app in your browser (the first time, it installs what
   it needs). **Nothing runs until you press Run model.** A run takes 20–60 seconds and then:
   - grades finished picks
   - prices the current NFL week and the next 3 days of soccer, with injuries applied
   - shows each recommended bet as a card: the bet, the DraftKings/FanDuel price, the stake in
     units and dollars, the edge, and the worst price still worth taking
   - logs those bets in the pick tracker
3. Tabs:
   - **Games**: a scoreboard with team logos, records, kickoff times or live scores, filterable by
     league. Tap a game to open its page: the model's projection next to the books (e.g. "Model: PHI by
     1.0 · Books: PIT by 3.5"), win-probability bars, and every book's spread, total and moneyline with the
     model's cover chance and EV. NFL pages add the most likely final margins and QBs/injuries; soccer
     pages add expected goals, likely scores, BTTS and lineups/absences.
   - **Bets to take**: the recommended bets as cards. Tap one to jump to its game page.
   - **Pick tracker**: record, profit, ROI and closing-line value. **Settings**: unit size, edges, leagues.
4. Close the black window when you're done; that shuts the app down.

### Look and feel

- **Light and dark mode.** It follows your device. The sun/moon button in the top bar switches it and
  remembers your choice in that browser.
- **One design for the PC app and the phone page.** Colours, fonts (Inter) and animation all live in
  `betmodel/theme.py`. To rename the app, change `APP_NAME` there.
- **Smooth touches.** Tabs fade in, a tapped game card highlights straight away, game pages slide in on
  phones, live scores bump when they change, probability bars fill in, and the beaten team is dimmed
  once a game is final. Animations are switched off if your device asks for reduced motion.
- **Testing changes safely.** `python -m betmodel app --no-publish --port 8775` runs a second copy that
  never uploads the phone page.

### Use it on your phone

The app runs on your PC; your phone opens it in its browser. The PC has to be on and awake, with
the app window open.

- **Same Wi-Fi:** double-click **`app_phone.bat`** instead of `app.bat`. It prints an address like
  `http://192.168.1.11:8765`; type that into your phone's browser. The first time, Windows asks
  about Python: allow it on **Private networks**.
- **From anywhere (cellular, work, travelling):** use **Tailscale**, a free private network between
  your own devices:
  1. Install Tailscale on the PC (tailscale.com/download) and sign in (Google / Microsoft / Apple).
  2. Install the Tailscale app on your phone and sign in with the **same account**.
  3. Run `app_phone.bat`. It now also prints a `http://100.x.x.x:8765` address (marked "works from
     anywhere"). Open that on your phone, with Tailscale switched on in the phone app.
  4. Optional: in your phone browser use **Add to Home Screen** so it opens like an app.

  Only devices signed in to your Tailscale account can reach it. Don't use a public tunnel (ngrok,
  Cloudflare): anyone with the link could run the model, use up your Odds API credits and change settings.
- Keep the PC from sleeping while you're out (Windows Settings → System → Power → Sleep: Never when
  plugged in).

On a phone, tap a game and its page opens full screen; "‹ All games" goes back.

### View the latest run from anywhere, PC off

Every run saves a read-only copy of the results to `snapshot/index.html`: the same scoreboard and
game pages, the recommended bets and your tracker. With a free GitHub account it is also uploaded
automatically to a web page you can open from any phone, even when the PC is off.

One-time setup (about 5 minutes):
1. Create a free account at github.com.
2. Create a new repository (the **+** at top right → New repository). Give it a hard-to-guess name such
   as `bm-k7r2xq`, choose **Public**, and tick **Add a README file**.
3. In that repo: **Settings → Pages → Deploy from a branch → `main` / `(root)` → Save**.
4. Create an access token: github.com/settings/personal-access-tokens/new. Choose Repository access:
   **Only select repositories** → your repo, then Permissions: **Contents → Read and write** → Generate. Copy it.
5. Double-click **`keys.bat`**. Press Enter to skip the first two prompts, paste the token, then type the
   repo as `yourname/bm-k7r2xq`. The check prints your page address.
6. Run the model once. The notes show `Snapshot published: https://yourname.github.io/bm-k7r2xq/`.
   Bookmark it or add it to your phone's home screen. It refreshes about a minute after each run.

Privacy: free GitHub Pages sites are public, so anyone with the exact link can see the page. That's
why the repo name should be unguessable. The page contains the picks, prices and your tracker,
never your keys or settings. Without the GitHub runs below, the page is frozen at the PC's last run.

### Run it from anywhere (GitHub runs the model for you)

Because the odds are free (no keys), the whole model can run on GitHub's computers, free for a public
repo. Once it's set up:

- **The model runs by itself every 2 hours** (fresh DraftKings and Kalshi prices, new picks), and **every 30
  minutes in the 3 hours before any kickoff**, when confirmed soccer lineups, NFL inactives and late price moves
  come out. **Scores, grading and results update every 30 minutes** while games are on. The PC can be off.
- **The phone page has a Run button.** One tap starts a full run; a card shows each step and the page
  reloads with the new results in about a minute. The first time, it asks for a GitHub key, which it keeps
  on that phone only. Make one at github.com/settings/personal-access-tokens/new: Only select repositories →
  your repo, Permissions → **Actions: Read and write**, nothing else. *Run options* (in the yellow banner)
  has Update scores now and Remove the key.
- An open page notices when a newer one is published and offers to refresh (or just loads it when you come
  back to it). Live scores still come straight from ESPN while it's open.
- **The PC app follows along.** Run model, Update scores, Grade and Rebuild history start the run on GitHub
  and copy the results back; opening or refocusing the app copies anything newer (a scheduled run, or one
  started on the phone). Settings → Save also sends the settings to GitHub. Settings → *Run on GitHub*
  switches the buttons back to running on the PC.

One-time setup: the PC's key (`keys.bat`) needs **Contents, Actions, Workflows and Pages: Read and write**
on the repo (edit the key on GitHub; it stays the same key). Then run `python -m betmodel cloud setup`. It
checks that none of your keys are in anything it uploads, uploads the code, the workflow
(`.github/workflows/oddsea.yml`), `settings.json` and this PC's data, switches the page to "GitHub
Actions", and starts the first run. `python -m betmodel cloud setup --dry-run` does the checks only.

How it fits together: the database travels between runs as a file on the repo's **oddsea-data** release
(the newest 4 are kept; delete the release and the history goes with it). config.json, the logs and the
PC's own folders never leave the PC; `settings.json` holds only unit, bankroll, edges and competitions.
The repo is public, so the code and that data file are too, like the page. Other commands:
`python -m betmodel cloud status | pull | run | scores | off | on`.

### Two buttons: free score update vs full run

Both buttons are free: odds come from DraftKings (via ESPN) and Kalshi, with no API key and no credits.

| | **Update scores (free)** | **Run model (free)** |
|---|---|---|
| Data used | ESPN only | DraftKings via ESPN + Kalshi + FotMob + FPL + nflverse + football-data |
| API credits | **0** | **0** |
| Time | a few seconds | 30–60 seconds |
| Does | live/final scores, grades your picks, compares each finished game with what the model and the books expected, refreshes the phone page | re-prices every game, finds new bets, saves new predictions |

Also available without the app: double-click `update_scores.bat` (free) or `run.bat` (full).

**Scores update themselves (free, ESPN only):**
- **App on the PC:** opening the app, or coming back to its tab, runs the free score update (at most
  every 2 minutes, and every 3 minutes while a game on screen is live). It never re-runs the model or
  uses Odds API credits. These automatic updates upload the phone page at most every 15 minutes;
  pressing a button always uploads.
- **Phone page (PC can be off):** the page fetches live scores straight from ESPN when you open it,
  when you switch back to it, and every 2 minutes while a game is live. Cards show the live score, and
  when a game ends its bet is marked HIT / MISS and the prediction ✓ / ✗. The Results tab gets a
  "Finished since this page was published" section until the PC's next update folds them into the
  records.

Every game also shows the **model's prediction**, after **simulating the game 100,000 times** from the
model's own numbers (`betmodel/simulate.py`): the outcome that came up most often, with its share of the
runs (e.g. "49ers 23-20 (56%)"). In a dead heat (top two within 0.5% of the runs) the model's exact
favourite decides, so simulation noise can't flip a coin-flip call.
- **NFL** games are played drive by drive (touchdowns, field goals, safeties, overtime), then the simulated
  games are reweighted so their final margins follow the model's own margin distribution, key numbers
  included. The score shown is the most common one near each team's expected points (exact NFL scores are
  spread thin, and 20-17 tops the raw list for almost any matchup).
- **Soccer** goals are drawn from the model's score grid.
- **Simulated spread and total** for every game: the lines where each side wins about half the simulations
  (NFL in half points; soccer as a goal handicap in quarter goals). Shown on each game card next to
  DraftKings' line ("Sim CIN -4 · DK CIN -6"), in the game page headline and the every-game table, with how
  often each side covered DraftKings' spread in the simulations.

The game page's "Simulated 100,000 times" section has the counts, the spread and total, and the most common
exact scores. Results are saved, so each game is simulated once. Simulating doesn't make the model more
accurate: it already computes the exact chances. NFL win chances in the simulation follow the model's
margin distribution, which for favourites runs a point or a few below its moneyline win probability. And every bet has a **why** line:
the model's own chance, the books' chance, what the price needs, and the blended chance the edge is
measured with. The prediction and the bet can differ. A bet pays on price, so the value is often on
the team the model expects to lose, just by less than the books think (e.g. predicting Lions 26-24
while the bet is Bucs +183).

The **Results** tab (in the app and on the phone page) shows one bet per finished game and whether it hit:
- the **recommended** bet if there was one; otherwise the **lean** (best value, edge under your
  minimum); otherwise the **least-bad** bet, which is the outcome the model *itself* thinks is most
  likely to happen (its own numbers, e.g. a favourite to win, or the underdog plus points when the
  model expects a close game)
- a **prediction** column: the model's call with ✓ / ✗, and a "predictions right" record
- the record at the top (all sports, then NFL and soccer), and what you'd be up or down with exactly
  1 unit on every bet
- a section per sport with its own record, split by recommended / lean / least-bad, and every game
  in green (hit) or red (miss) with the 1-unit profit, a running total, and what the model and the
  books expected

Predictions are frozen at kickoff. A game that had already started when the model ran never counts,
so the record can't be flattered.

### Model history (every past game: predicted vs what happened)

The **Model history** tab (also on the phone page) covers about 5,900 games: NFL 2018 to now, and
the Premier League, La Liga, Serie A, Champions League and Europa League from 2024-25 to now. Past
seasons are replayed week by week, with the model refit using only games played before that week,
so it never sees the result it is predicting. Games since you started running it use the
predictions saved before kickoff (marked *live*).

Filter by sport, league and season to see:
- how often the model picked the winner, and its average miss, side by side with the closing line
- a **calibration chart**: whether things the model called 30% really happened about 30% of the time
- the running profit or loss from betting every time the model disagreed with the line (2+ points
  in the NFL, 5+ percentage points in soccer)
- a by-season table and a game-by-game log

**Rebuild history** (free, about 3 minutes, no API credits, logs no picks) adds newly finished games.
From a terminal: `.\betmodel.cmd history build`, then `.\betmodel.cmd history` for a text summary. Use the full run when you want new bets; use the free update as
often as you like during and after games.

**Stakes:** 1 unit = $10 by default (change it in Settings). Each bet is sized by quarter-Kelly and
rounded to the nearest half unit, from 0.5u to 3u.

`run.bat` and `picks.bat` still work if you prefer the text version. Settings live in
**`config.json`**: unit, bankroll, minimum edge, leagues, and the two optional API keys below.

### Optional free keys (recommended)

| Key in `config.json` | What it adds | Sign up |
|---|---|---|
| `odds_api_key` | **Not used by default.** Odds are free (DraftKings via ESPN, Kalshi as the sharp price). To switch back to The Odds API (DraftKings, FanDuel, Pinnacle with update times; ~13 credits a run), add `"odds_source": "odds_api"` to config.json. | the-odds-api.com, free plan 500 credits/month |
| `api_football_key` | Injuries for La Liga, Serie A, UCL, UEL, and **confirmed lineups** about an hour before kickoff. Without it, soccer injuries cover the Premier League only (via FPL). | dashboard.api-football.com, free plan 100 requests/day (the code caps itself at 90) |

To add keys, double-click **`keys.bat`**, paste each key, and it tests them without using any
quota. From a terminal: `.\betmodel.cmd keys set odds YOURKEY`, then `.\betmodel.cmd keys` to re-check.
Use the key from api-football.com's own dashboard, not the RapidAPI version, which uses a
different key and header.

The API-Football free plan may not cover the current season. If it doesn't, the run prints the
API's error message and carries on without it.

## Where the data comes from (all free)

| Data | Source | Refresh |
|---|---|---|
| Soccer results, shots, xG, closing odds (Betfair Exchange; Pinnacle until early 2026, when football-data stopped carrying it) | football-data.co.uk | 6 h cache |
| UCL / UEL / UECL fixtures and results | fixturedownload.com | 6 h |
| Live scores, game status, team logos/records, **NFL injury statuses** (final Friday designations) | ESPN public feeds | 3 min / 1 h |
| **Soccer injuries, suspensions, predicted + confirmed lineups** (all 5 competitions) | FotMob public feed | 2 h (15 min near kickoff) |
| EPL injuries, chance of playing, player xG/xA | Fantasy Premier League API | 2 h |
| Other leagues' injuries + confirmed lineups (only on a paid plan) | API-Football (key) | 3 h / 15 min |
| NFL schedule, scores, closing lines, starting QBs | nflverse | 3 h |
| NFL official injury reports + snap counts | nflverse | 3 h |
| **DraftKings prices** (NFL moneyline / spread / total; soccer result and main goal total) | ESPN public scoreboards (DraftKings is ESPN's odds provider) | 3 min |
| **Sharp prices** (in place of Pinnacle): game winner, spread and total markets, used only when the bid/ask gap is 4 cents or less | Kalshi public market data (official API, no key); in spot checks within ~1–2 points of Pinnacle's no-vig prices | 3 min |
| DraftKings / FanDuel / Pinnacle with update times (optional, off by default) | The Odds API (key, credits) | 1 h |
| Kickoff wind forecast at open-air NFL stadiums | Open-Meteo (free, no key) | 3 h |
| Player minutes, substitutions, xG / xA for every match since 2023-24 (lineup model) | FotMob public feed | new matches each run |

Everything is cached under `data/`. The local SQLite database is `data/betmodel.db`.

## How injuries and lineups are used

- **NFL** (fitted from 2016–2026 data): each player listed Out/Doubtful/Questionable is
  weighted by his recent snap share and summed by position group. The model learned what each
  group costs, e.g. a starting offensive lineman or receiver out is about −0.6 points, and a
  missing defensive starter is worth +0.3 to +0.8 to the opponent. Before Friday's final report,
  practice participation is used instead. QBs carry their own fitted value that follows them
  across teams. Backtest effect: margin error 12.91 → 12.89, totals 13.24 → 13.21.
- **NFL weather:** wind costs about 0.23 points of total per mph above a normal 7 mph day (fitted
  on past games; a 20 mph day is about -3 points). Domes add a little. Backtest: totals error
  13.34 → 13.27. Live runs use the kickoff forecast; retractable roofs are treated as closed.
  Tested and rejected (no improvement): divisional-game adjustments, margin recalibration, and
  rest/fixture congestion for soccer.
- **Soccer lineups (player-based, fitted):** every player is rated from FotMob match data since
  2023-24 (about 5,000 matches across the 5 competitions): starts, minutes, substitutions, xG and
  xA. Before each match the model compares the starting XI (FotMob's predicted XI, or the confirmed
  one about an hour before kickoff) with the team's usual side and adjusts both teams' expected
  goals. What proved to matter is **how many regulars are missing**: each regular left out (about one
  player's worth of the team's usual minutes) costs that team about 4.5% of its expected goals and
  gives the opponent about 4.5%. Walk-forward test on 2,398 matches: log-loss 0.9667 → 0.9643, with
  gains in the Premier League, La Liga and the Champions League, none in Serie A and a slight loss
  in the Europa League. Player ratings built from xG + xA and on-pitch xG impact were also tested
  and added nothing beyond this. With the lineup model in, the best blend with the sharp market is
  5% model (was 10%). When a lineup is available it replaces the injury list below, because the XI
  already reflects who is missing. Clubs seen only in Europe aren't adjusted. New matches are added
  on each Run model (a few seconds). Confirmed lineups come out about an hour before kickoff, so a
  run then gets the most out of this.
- **Soccer injuries, when no lineup is available** (a reasoned heuristic, not fitted, because there is no free historical injury
  data): a missing player removes half of his share of the team's xG + 0.7·xA, and missing
  defenders/keepers raise the opponent's xG by up to 8%. Confirmed lineups mark regulars left
  out of the XI as absent. Multipliers are capped at ±25%.

## Pick tracking

Every pick is stored with its price, book, model probability, edge and stake. After the game
it is graded automatically:

- **Result and profit/loss**, with pushes handled
- **Closing-line value (CLV)**: your price against the sharp close (soccer: Pinnacle/Betfair;
  NFL: closing spread/total/moneyline). This is the number to watch. Over a few dozen bets,
  wins and losses are mostly luck, but consistently beating the close means a real edge.
- The report splits results by sport, market and price source (DK/FD vs. proxy).

**The model's best bet on every other game** is tracked too, separately, at a notional 0.5 unit:
*leans* (positive edge, below your minimum) and *least-bad* bets (every price is negative EV; the
side the model dislikes least). Each Run model before kickoff replaces a game's entry with the latest
best bet and price, so there is one per game. They are graded like everything else but never
counted in the main record. The Pick tracker shows their record, profit, ROI and CLV in their own section.

Log a bet you actually placed:
```powershell
.\betmodel.cmd picks add --sport nfl --date 2026-11-22 --home SF --away MIN --market SPREAD --selection MIN --line 3 --odds -105 --stake 25 --book draftkings
```
```powershell
.\betmodel.cmd picks add --sport soccer --date 2026-11-22 --home Liverpool --away "Man United" --market 1X2 --selection H --odds -120 --stake 20 --book fanduel
```

## When it recommends a pick

Only DraftKings prices are considered (your sportsbook; to add another, put it in `odds_api.BETTABLE`).
A bet is RECOMMENDED only when a DraftKings price passes all of these (rules in
`slate.PICK_RULES`; override any of them with a `"pick_rules"` entry in config.json):

1. **Price, not opinion.** It beats the sharp books' no-vig price by the minimum edge (default 3%) on its
   own. The model can veto a bet (by lowering the blended chance) but can't create one: against the
   closing line its own view adds nothing (NFL 2018–26: spread leans 52.2% at -110; soccer leans -41.9u).
2. **No longshots.** Nothing longer than +300 in the NFL or +500 in soccer, and the win chance must beat
   the price's break-even by at least 1.5 points. A 5% "edge" on a 9% shot is within the error of
   removing the vig. (This is what made the ARI +980 pick.)
3. **Fresh prices.** Skipped when the sharp price is more than 30 minutes older than the book's price
   (The Odds API's update times), since a stale sharp line makes a fake edge. With the free odds there
   are no DraftKings update times, so instead a Kalshi price only counts when its bid/ask gap is 4 cents
   or less; a thin or stale Kalshi market means no sharp price, and so no recommendation.

Stakes use the smaller of the blended and sharp-only chances. Games that don't pass still show a LEAN
(with the reason it wasn't recommended) or a LEAST-BAD bet; those aren't expected to profit. Expect few
or no picks on many days; that's by design.

**Price log.** Every run saves every book's pre-game price with the edge the model saw (about 300 prices
a run). **Kalshi closing prices:** on the first run or free score update after kickoff, each logged
DraftKings price, and each of your bets (recommended, leans, least-bad), is graded against Kalshi's last
price before kickoff: expected profit per $1 at that sharp closing price, including the push chance on
whole-number lines. This covers every competition, the Champions and Europa League included, and shows as
"CLV Kalshi" in the Pick tracker. Games Kalshi doesn't list fall back to the free closing lines: when games
are final, each DraftKings price is graded against the closing price
(free: nflverse for NFL the morning after, football-data.co.uk for the domestic soccer leagues about
twice a week; Champions/Europa League have no free closing odds). The Pick tracker shows average CLV by
edge size, market and time before kickoff; after a few weeks that is the evidence for changing the
minimum edge, not a weekend's results. From a terminal: `.\betmodel.cmd pricelog`.

## Results (walk-forward backtests)

| | Model | Sharp closing line |
|---|---|---|
| Soccer 1X2 log-loss, big-5 leagues 2025-27 (2,252 games) | 0.9775 | 0.9695 |
| Soccer O/U 2.5 log-loss | 0.6756 | 0.6680 |
| UCL/UEL log-loss (516 games) | 0.9135 | base rate 1.0109 |
| NFL margin RMSE 2023-26 (1,006 games) | 12.89 | 12.63 |
| NFL totals RMSE 2019-26 with wind (2,112 games) | 13.27 | 13.13 |
| NFL moneyline log-loss | 0.6241 | 0.6080 |

The models come close to the sharp close but don't beat it. No public model does.

**So the predictions blend the model with the market.** Every headline call, simulated spread and total, and
the Results page's prediction record use the same mix the prices use (NFL spreads 10% model / 90% market,
totals 15% / 85%, soccer 5% / 95%; "model alone" is shown underneath). On the history table's past games:

| | Model alone | Books (closing line) | Blend |
|---|---|---|---|
| NFL winners right (2,388 games) | 65.7% | 66.4% | 66.4% |
| NFL average miss on the margin | 10.03 | 9.81 | 9.81 |
| NFL average miss on the total | 10.53 | 10.41 | 10.41 |
| Soccer results right (2,614 matches) | 53.3% | 54.2% | 54.4% |

That's as accurate as the books, not more: beating them on accuracy would take data they don't have. The
money comes from the recommended bets, where DraftKings is out of line with Kalshi.

### Improvements tested (kept only if they helped on games the tuning never saw)

| Idea | Result | Kept? |
|---|---|---|
| NFL wind at kickoff | totals error 13.34 → 13.27 | yes |
| Soccer lineups (missing regulars) | log-loss 0.9667 → 0.9643 | yes |
| Soccer: forget old results faster (time decay 0.008 → 0.012) | better in both the tuning period (2024-25) and the holdout (2025-26+), by 0.0007 | yes |
| NFL pace, pass rate, efficiency per play | totals slightly worse (13.28 → 13.30+) | no |
| NFL divisional games, margin recalibration | slightly worse | no |
| NFL settings re-tune (choose on 2019-23, test on 2024-26) | nothing improved both periods | no change |
| Soccer settings re-tune (other settings) | nothing improved both periods | no change |
| Soccer rest days / fixture congestion | no change (0.96937 → 0.96934) | no |
| Real xG (FotMob) for Champions / Europa League games | log-loss slightly better, Brier slightly worse: noise | no |
| Player ratings from xG + xA, on-pitch xG impact | nothing beyond the missing-regulars signal | no |

One more honest check: choosing the NFL model/market blend weights on 2019-23 and testing on 2024-26
showed the model adds essentially nothing to the *closing* line (spread error 12.403 closing line
only vs 12.410 with 10% model). The weights were left as they are: the difference is tiny, and bets
are placed before the close, when the model may matter more. Free NFL data has only closing lines, so
that can't be measured.

## Other commands

```powershell
.\betmodel.cmd soccer predict Arsenal Chelsea
```
```powershell
.\betmodel.cmd soccer predict "Man City" Napoli --comp UCL --odds -250 +400 +600 --sharp 1.42 5.2 7.4
```
```powershell
.\betmodel.cmd nfl predict SF MIN --spread -3 --total 46.5 --ml -150 +130
```
```powershell
.\betmodel.cmd nfl injuries --team SF
```
`soccer fixtures`, `soccer ratings`, `nfl week`, `nfl ratings`, `soccer backtest`, `nfl backtest`
and `picks report|settle|export|delete --id N` are also available. Add `--log` to `soccer fixtures`
/ `nfl week` to save their picks.

## Known limits

- ESPN and FotMob are unofficial public feeds: free and live, but they can change without notice. If one
  breaks, the run continues without it and says so in the notes.
- Outside the Premier League, FotMob gives no positions or minutes for injured players, so their
  importance is estimated from market value relative to the team's average starter.
- Soccer injury effects are a heuristic. NFL injury effects are fitted, but small.
- UCL/UEL prices can't be checked against odds historically, so they are the least verified.
- NFL week 18 (rested starters) and early-season weeks are the noisiest.

Bet responsibly. These are probability estimates, not guarantees. Check that sports betting is
legal where you are. Help: 1-800-GAMBLER.
