# Spot On Daily: question bank

A question ships only when two independent sources agree.

## Setup
```
pip install requests nba_api
```

## Daily sets
Every set in `questions.json` gets a `date` when it's scheduled; it plays that day and is never reused.

- **Make your own:** `python spot_on_bank.py make --sport nfl` asks for five questions (easiest first) for
  tomorrow, or pass `--date 2026-10-20`. It refuses questions already in the bank, fetches and verifies
  the answers right away, and lets you re-enter any that fail. `--replace` swaps out your earlier set for that day.
- **Everything else is random:** days without a hand-made set get a random verified set from the pool.
  If your hand-made set isn't verified, a random set covers the day until you fix it.
- `python spot_on_bank.py schedule` shows what plays over the next 14 days (`*` = hand-made) and warns when a
  sport's pool is empty. `export` does the same scheduling, then writes `bank.js`.

## Workflow
1. **Write specs** in `questions.json`: league, player, season, stat. No answers.
   - NFL/MLB seasons: `"2022"`. NBA/NHL seasons: `"2015-16"`.
   - Stat keys: `python spot_on_bank.py stats`
   - Each set needs 5 questions, easiest first (weights 1, 1, 2, 3, 3).
2. **Fetch source #1:** `python spot_on_bank.py fetch`
   (NFL: nflverse, 1999 onward. NBA: stats.nba.com. NHL: NHL API. MLB: MLB Stats API.)
3. **Check source #2 automatically:** `python spot_on_bank.py verify` looks each question up
   on ESPN's public stats API and fills in `reference` (value, url, checkedOn, `"by": "ESPN (auto)"`).
   Answers you already checked are kept; `--refresh` re-checks them, `--set nfl-0002` does one set.
   Anything ESPN can't cover (older seasons, a few missing players) prints FAIL: check those by hand
   with the `reference.hint` link (Sports Reference search) and fill in `reference` yourself.
   If a name matches two players, put the right ESPN id in `espnId` (from the player's ESPN URL).
4. **Validate:** `python spot_on_bank.py check --mark`
   - ERROR: must fix (mismatch, bad season format, unknown stat, wrong set size)
   - PENDING: still needs a fetch or a reference check
   - WARN: worth a look (duplicate question, missing reference url)
5. **Export for the game:** `python spot_on_bank.py export` schedules the next 14 days (`--days` to change)
   and writes `bank.js` with each day's verified sets.

## Auto-generate sets (every sport)
`python spot_on_bank.py generate --league nhl --count 20` builds sets from season leaderboards
(`--league` is nfl, nba, nhl, mlb, mix, or all). Easy: league leaders in headline stats, recent seasons.
Medium: top 3 in second-tier stats. Hard: top 5 in lesser-watched stats, or headline stats from older seasons.
Mixed sets draw from all four leagues, at most 2 questions per league.

Every new set then goes through `fetch` and `verify` automatically. A question that fails is swapped
for another one from the same tier (up to 4 rounds); sets that still don't pass are dropped, so only
verified sets are added. Only league leaders get an automatic fact. Nothing already in the bank is reused.
`--no-check` skips the checks, `--seed` fixes the shuffle, `--since` / `--to` limit the seasons.

Leaderboard sources and earliest season: NFL nflverse (1999), MLB Stats API (1960),
NHL stats API (1985-86), NBA stats.nba.com season totals (1996-97; slow, the site needs pauses).

## Other commands
- `python spot_on_bank.py find "Dave Schultz" --league NHL` lists player IDs. If a name matches
  more than one player, put the right one in `playerId`.
- `python spot_on_bank.py fetch --set nfl-0002` fetches one set. `--refresh` re-fetches filled values.

## Optional per-question fields
- `tolerance`: `{"pct": 0.15, "floor": 3}` overrides the default leniency.
- `fact`: one-line fun fact for the reveal screen.
- `espnId`: ESPN player id, when `verify` says several players share the name.

## Known limits
- ESPN gaps: NBA before 1969-70, some NFL seasons before 2014 for certain players, some retired NHL players.
- NFL data starts in 1999. For older seasons, choose a different question for now.
- NBA steals and blocks start in 1973-74. NFL sacks can be half-sacks, so `check` rejects non-whole answers.
- If a source and the reference disagree, find out why (revised records, traded players,
  playoffs included) before using the question.
