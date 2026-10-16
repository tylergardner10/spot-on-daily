#!/usr/bin/env python3
"""
Spot On Daily question bank tool.

Workflow (two-source rule: a question only ships when two independent sources agree):
  1. Write question specs in questions.json: league, player, season, stat. No answers.
  2. python spot_on_bank.py fetch     -> pulls each answer from a stats dataset/API (source #1)
  3. python spot_on_bank.py verify    -> fills "reference" from ESPN automatically (source #2);
     anything ESPN can't cover is checked by hand on Sports Reference
  4. python spot_on_bank.py check     -> validates structure and flags any disagreement
  5. python spot_on_bank.py export    -> schedules the coming days and writes bank.js for the game

Daily sets:
  python spot_on_bank.py make --sport nfl [--date 2026-10-16]  -> hand-make a set for a day (default tomorrow)
  python spot_on_bank.py schedule                             -> show the coming days; fills gaps at random
  Days without a hand-made set get a random verified set. A set is used once, then retired.

Other commands:
  python spot_on_bank.py find "Wayne Gretzky" --league NHL   -> look up player IDs
  python spot_on_bank.py stats                               -> list stat keys per league

Data sources (source #1):
  NFL  nflverse season files (GitHub), 1999 onward
  NBA  stats.nba.com via the nba_api package (pip install nba_api)
  NHL  NHL public API (api-web.nhle.com)
  MLB  MLB Stats API (statsapi.mlb.com)

Requires: Python 3.9+, requests. nba_api only for NBA questions.
"""

import argparse
import contextlib
import csv
import datetime as dt
import io
import json
import os
import random
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Install requests first: pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
DEFAULT_BANK = os.path.join(HERE, "questions.json")
SPORTS = ["nfl", "nba", "nhl", "mlb", "mix"]
LEAGUES = ["NFL", "NBA", "NHL", "MLB"]
HEADERS = {"User-Agent": "SpotOnDaily-bank/1.0 (personal project)"}

# ---------------------------------------------------------------------------
# Stat registry: game label, where the number comes from, default cushion (floor).
# Only whole-number counting stats: the game takes typed whole numbers.
# ---------------------------------------------------------------------------
STATS = {
    "NFL": {
        "passing_yards":   {"label": "passing yards",   "cols": ["passing_yards"], "floor": 100},
        "passing_tds":     {"label": "passing TDs",     "cols": ["passing_tds"], "floor": 3},
        "interceptions_thrown": {"label": "interceptions thrown", "cols": ["passing_interceptions"], "floor": 2},
        "completions":     {"label": "completions",     "cols": ["completions"], "floor": 10},
        "rushing_yards":   {"label": "rushing yards",   "cols": ["rushing_yards"], "floor": 100},
        "rushing_tds":     {"label": "rushing TDs",     "cols": ["rushing_tds"], "floor": 2},
        "carries":         {"label": "carries",         "cols": ["carries"], "floor": 10},
        "receptions":      {"label": "receptions",      "cols": ["receptions"], "floor": 5},
        "receiving_yards": {"label": "receiving yards", "cols": ["receiving_yards"], "floor": 100},
        "receiving_tds":   {"label": "receiving TDs",   "cols": ["receiving_tds"], "floor": 2},
        "scrimmage_yards": {"label": "yards from scrimmage", "cols": ["rushing_yards", "receiving_yards"], "floor": 100},
        "total_tds":       {"label": "rushing + receiving TDs", "cols": ["rushing_tds", "receiving_tds"], "floor": 2},
        "def_interceptions": {"label": "interceptions", "cols": ["def_interceptions"], "floor": 1},
        "solo_tackles":    {"label": "solo tackles",    "cols": ["def_tackles_solo"], "floor": 5},
        # Sacks can be half-sacks (e.g. 12.5). check() rejects non-whole answers.
        "sacks":           {"label": "sacks",           "cols": ["def_sacks"], "floor": 1},
    },
    "NBA": {
        "points":         {"label": "points",          "col": "PTS", "floor": 100},
        "rebounds":       {"label": "rebounds",        "col": "REB", "floor": 50},
        "assists":        {"label": "assists",         "col": "AST", "floor": 40},
        "steals":         {"label": "steals",          "col": "STL", "floor": 10},   # tracked from 1973-74
        "blocks":         {"label": "blocks",          "col": "BLK", "floor": 10},   # tracked from 1973-74
        "threes_made":    {"label": "three-pointers made", "col": "FG3M", "floor": 10},
        "free_throws_made": {"label": "free throws made", "col": "FTM", "floor": 20},
        "games":          {"label": "games played",    "col": "GP", "floor": 3},
        "triple_doubles": {"label": "triple-doubles",  "gamelog": 3, "floor": 3},
        "double_doubles": {"label": "double-doubles",  "gamelog": 2, "floor": 3},
    },
    "NHL": {
        "goals":       {"label": "goals",            "field": "goals", "floor": 3},
        "assists":     {"label": "assists",          "field": "assists", "floor": 3},
        "points":      {"label": "points",           "field": "points", "floor": 5},
        "pim":         {"label": "penalty minutes",  "field": "pim", "floor": 20},
        "games":       {"label": "games played",     "field": "gamesPlayed", "floor": 3},
        "ppg":         {"label": "power-play goals", "field": "powerPlayGoals", "floor": 2},
        "gwg":         {"label": "game-winning goals", "field": "gameWinningGoals", "floor": 1},
        "shots":       {"label": "shots on goal",    "field": "shots", "floor": 20},
        "goalie_wins": {"label": "wins (goalie)",    "field": "wins", "floor": 3},
        "shutouts":    {"label": "shutouts",         "field": "shutouts", "floor": 1},
    },
    "MLB": {
        "home_runs":    {"label": "home runs",      "group": "hitting",  "field": "homeRuns", "floor": 3},
        "hits":         {"label": "hits",           "group": "hitting",  "field": "hits", "floor": 8},
        "rbi":          {"label": "RBIs",           "group": "hitting",  "field": "rbi", "floor": 8},
        "runs":         {"label": "runs",           "group": "hitting",  "field": "runs", "floor": 8},
        "doubles":      {"label": "doubles",        "group": "hitting",  "field": "doubles", "floor": 3},
        "triples":      {"label": "triples",        "group": "hitting",  "field": "triples", "floor": 2},
        "stolen_bases": {"label": "stolen bases",   "group": "hitting",  "field": "stolenBases", "floor": 6},
        "walks":        {"label": "walks",          "group": "hitting",  "field": "baseOnBalls", "floor": 8},
        "strikeouts_batting": {"label": "strikeouts (batting)", "group": "hitting", "field": "strikeOuts", "floor": 8},
        "strikeouts":   {"label": "strikeouts",     "group": "pitching", "field": "strikeOuts", "floor": 15},
        "wins":         {"label": "wins",           "group": "pitching", "field": "wins", "floor": 2},
        "saves":        {"label": "saves",          "group": "pitching", "field": "saves", "floor": 3},
        "complete_games": {"label": "complete games", "group": "pitching", "field": "completeGames", "floor": 2},
        "shutouts":     {"label": "shutouts",       "group": "pitching", "field": "shutouts", "floor": 1},
    },
}

# Default leniency by question position (easy -> hard). Can be overridden per question.
DEFAULT_PCT = [0.10, 0.10, 0.12, 0.20, 0.20]
REF_SITES = {
    "NFL": "https://www.pro-football-reference.com/search/search.fcgi?search=",
    "NBA": "https://www.basketball-reference.com/search/search.fcgi?search=",
    "NHL": "https://www.hockey-reference.com/search/search.fcgi?search=",
    "MLB": "https://www.baseball-reference.com/search/search.fcgi?search=",
}


class FetchError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP with a small on-disk cache, so re-runs don't hammer anyone's servers.
# ---------------------------------------------------------------------------
def http_get(url, as_json=True, cache_days=30):
    os.makedirs(CACHE, exist_ok=True)
    key = urllib.parse.quote(url, safe="")[:200]
    path = os.path.join(CACHE, key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < cache_days * 86400:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            raise FetchError("not found: " + url)
        r.raise_for_status()
        text = r.text
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        time.sleep(0.5)  # be polite
    return json.loads(text) if as_json else text


def norm(name):
    """Comparable name: lowercase letters/digits, without a trailing Jr./Sr./II/III/IV."""
    words = name.lower().replace(",", " ").split()
    if len(words) > 2 and words[-1].strip(".") in ("jr", "sr", "ii", "iii", "iv"):
        words = words[:-1]
    return "".join(ch for ch in "".join(words) if ch.isalnum())


def season_start(label):
    """'2015-16' -> 2015, '2022' -> 2022."""
    return int(str(label).split("-")[0])


# ---------------------------------------------------------------------------
# NFL: nflverse season files (regular season), 1999 onward
# ---------------------------------------------------------------------------
_nfl_rows = {}


def nfl_rows(season):
    if season not in _nfl_rows:
        url = ("https://github.com/nflverse/nflverse-data/releases/download/"
               "stats_player/stats_player_reg_%d.csv" % season)
        try:
            text = http_get(url, as_json=False, cache_days=365)
        except FetchError:
            raise FetchError("nflverse has no %d season file (coverage starts in 1999)" % season)
        _nfl_rows[season] = list(csv.DictReader(io.StringIO(text)))
    return _nfl_rows[season]


def fetch_nfl(q, spec):
    season = int(q["season"])
    rows = nfl_rows(season)
    if q.get("playerId"):
        hits = [r for r in rows if r["player_id"] == q["playerId"]]
        if hits and norm(hits[0]["player_display_name"]) != norm(q["player"]):
            raise FetchError("playerId %s is %s, not %s. Clear playerId after changing the player"
                             % (q["playerId"], hits[0]["player_display_name"], q["player"]))
    else:
        hits = [r for r in rows if norm(r["player_display_name"]) == norm(q["player"])]
    if not hits:
        raise FetchError("no NFL player named %r in %d" % (q["player"], season))
    if len({r["player_id"] for r in hits}) > 1:
        raise FetchError("several NFL players named %r in %d; set playerId to one of %s"
                         % (q["player"], season, sorted({r["player_id"] for r in hits})))
    row = hits[0]
    total = 0.0
    for col in spec["cols"]:
        total += float(row.get(col) or 0)
    return total, row["player_id"], "nflverse"


# ---------------------------------------------------------------------------
# NBA: nba_api (stats.nba.com)
# ---------------------------------------------------------------------------
def nba_api():
    try:
        from nba_api.stats.static import players
        from nba_api.stats.endpoints import playercareerstats, playergamelog
    except ImportError:
        raise FetchError("NBA questions need nba_api: pip install nba_api")
    return players, playercareerstats, playergamelog


def nba_player_id(q):
    if q.get("playerId"):
        return int(q["playerId"])
    players, _, _ = nba_api()
    hits = [p for p in players.get_players() if norm(p["full_name"]) == norm(q["player"])]
    if not hits:
        raise FetchError("no NBA player named %r" % q["player"])
    if len(hits) > 1:
        raise FetchError("several NBA players named %r; set playerId to one of %s"
                         % (q["player"], [p["id"] for p in hits]))
    return hits[0]["id"]


def fetch_nba(q, spec):
    _, playercareerstats, playergamelog = nba_api()
    pid = nba_player_id(q)
    label = str(q["season"])
    time.sleep(0.8)  # stats.nba.com is touchy about rapid requests
    if "gamelog" in spec:
        df = playergamelog.PlayerGameLog(player_id=pid, season=label,
                                         season_type_all_star="Regular Season").get_data_frames()[0]
        if df.empty:
            raise FetchError("no NBA game log for %s in %s" % (q["player"], label))
        cats = ["PTS", "REB", "AST", "STL", "BLK"]
        need = spec["gamelog"]
        count = 0
        for _, g in df.iterrows():
            if sum(1 for c in cats if (g.get(c) or 0) >= 10) >= need:
                count += 1
        return float(count), str(pid), "stats.nba.com (game logs)"
    df = playercareerstats.PlayerCareerStats(player_id=pid, per_mode36="Totals").get_data_frames()[0]
    rows = df[df["SEASON_ID"] == label]
    if rows.empty:
        raise FetchError("no NBA regular season %s for %s" % (label, q["player"]))
    tot = rows[rows["TEAM_ABBREVIATION"] == "TOT"]  # traded mid-season: use the combined row
    row = tot.iloc[0] if not tot.empty else rows.iloc[0]
    if tot.empty and len(rows) > 1:
        return float(rows[spec["col"]].sum()), str(pid), "stats.nba.com"
    val = row[spec["col"]]
    if val is None or val != val:  # NaN: stat not tracked that season
        raise FetchError("%s not recorded for %s" % (spec["col"], label))
    return float(val), str(pid), "stats.nba.com"


# ---------------------------------------------------------------------------
# NHL: public NHL API
# ---------------------------------------------------------------------------
def nhl_search(name):
    url = ("https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=20&q="
           + urllib.parse.quote(name))
    return http_get(url)


def nhl_totals(pid, start, spec):
    data = http_get("https://api-web.nhle.com/v1/player/%s/landing" % pid)
    code = int("%d%d" % (start, start + 1))
    rows = [s for s in data.get("seasonTotals", [])
            if s.get("season") == code and s.get("gameTypeId") == 2 and s.get("leagueAbbrev") == "NHL"]
    if not rows:
        return None
    field = spec["field"]
    if any(field not in r for r in rows):
        raise FetchError("%s not available for that season" % field)
    return float(sum(r[field] for r in rows))  # sums across teams if traded


def fetch_nhl(q, spec):
    start = season_start(q["season"])
    if q.get("playerId"):
        ids = [str(q["playerId"])]
    else:
        ids = [str(p["playerId"]) for p in nhl_search(q["player"]) if norm(p.get("name", "")) == norm(q["player"])]
    if not ids:
        raise FetchError("no NHL player named %r" % q["player"])
    found = []
    for pid in ids:
        v = nhl_totals(pid, start, spec)
        if v is not None:
            found.append((pid, v))
    if not found:
        raise FetchError("no NHL regular season %s for %s" % (q["season"], q["player"]))
    if len(found) > 1:
        raise FetchError("several NHL players named %r played in %s; set playerId to one of %s"
                         % (q["player"], q["season"], [f[0] for f in found]))
    return found[0][1], found[0][0], "api-web.nhle.com"


# ---------------------------------------------------------------------------
# MLB: MLB Stats API
# ---------------------------------------------------------------------------
def mlb_totals(pid, season, spec):
    url = ("https://statsapi.mlb.com/api/v1/people/%s/stats?stats=yearByYear&group=%s&sportId=1"
           % (pid, spec["group"]))
    data = http_get(url)
    splits = []
    for block in data.get("stats", []):
        splits += [s for s in block.get("splits", []) if str(s.get("season")) == str(season)]
    if not splits:
        return None
    combined = [s for s in splits if "team" not in s]  # some responses include a combined row
    use = combined if combined else splits
    field = spec["field"]
    return float(sum(int(s["stat"].get(field, 0)) for s in use))


def fetch_mlb(q, spec):
    season = int(q["season"])
    if q.get("playerId"):
        ids = [str(q["playerId"])]
    else:
        data = http_get("https://statsapi.mlb.com/api/v1/people/search?names="
                        + urllib.parse.quote(q["player"]))
        ids = [str(p["id"]) for p in data.get("people", []) if norm(p.get("fullName", "")) == norm(q["player"])]
    if not ids:
        raise FetchError("no MLB player named %r (try their full legal name, or set playerId)" % q["player"])
    found = []
    for pid in ids:
        v = mlb_totals(pid, season, spec)
        if v is not None:
            found.append((pid, v))
    if not found:
        raise FetchError("no MLB %s %s stats for %s" % (season, spec["group"], q["player"]))
    if len(found) > 1:
        raise FetchError("several MLB players named %r played in %s; set playerId to one of %s"
                         % (q["player"], season, [f[0] for f in found]))
    return found[0][1], found[0][0], "statsapi.mlb.com"


FETCHERS = {"NFL": fetch_nfl, "NBA": fetch_nba, "NHL": fetch_nhl, "MLB": fetch_mlb}


# ---------------------------------------------------------------------------
# Source #2 (automatic): ESPN public stats API. Independent of the source #1 feeds.
# Each stat maps to (category names, column label). Categories None = any category
# that has the label (NHL splits categories by position: center, defense, ...).
# ---------------------------------------------------------------------------
ESPN_PATHS = {"NFL": "football/nfl", "NBA": "basketball/nba", "NHL": "hockey/nhl", "MLB": "baseball/mlb"}
ESPN_STATS = {
    "NFL": {
        "passing_yards": [(["passing"], "YDS")], "passing_tds": [(["passing"], "TD")],
        "interceptions_thrown": [(["passing"], "INT")], "completions": [(["passing"], "CMP")],
        "rushing_yards": [(["rushing"], "YDS")], "rushing_tds": [(["rushing"], "TD")],
        "carries": [(["rushing"], "CAR")], "receptions": [(["receiving"], "REC")],
        "receiving_yards": [(["receiving"], "YDS")], "receiving_tds": [(["receiving"], "TD")],
        "scrimmage_yards": [(["rushing"], "YDS"), (["receiving"], "YDS")],
        "total_tds": [(["rushing"], "TD"), (["receiving"], "TD")],
        "def_interceptions": [(["defensive"], "INT")], "solo_tackles": [(["defensive"], "SOLO")],
        "sacks": [(["defensive"], "SACK")],
    },
    "NBA": {
        "points": [(["totals"], "PTS")], "rebounds": [(["totals"], "REB")], "assists": [(["totals"], "AST")],
        "steals": [(["totals"], "STL")], "blocks": [(["totals"], "BLK")],
        "threes_made": [(["totals"], "3PT")], "free_throws_made": [(["totals"], "FT")],
        "games": [(["averages"], "GP")],
        "triple_doubles": [(["miscellaneous"], "TD3")], "double_doubles": [(["miscellaneous"], "DD2")],
    },
    "NHL": {
        "goals": [(None, "G")], "assists": [(None, "A")], "points": [(None, "PTS")], "pim": [(None, "PIM")],
        "games": [(None, "GP")], "ppg": [(None, "PPG")], "gwg": [(None, "GWG")], "shots": [(None, "SOG")],
        "goalie_wins": [(["goaltender"], "WINS")], "shutouts": [(["goaltender"], "SO")],
    },
    "MLB": {
        "home_runs": [(["career-batting"], "HR")], "hits": [(["career-batting"], "H")],
        "rbi": [(["career-batting"], "RBI")], "runs": [(["career-batting"], "R")],
        "doubles": [(["career-batting"], "2B")], "triples": [(["career-batting"], "3B")],
        "stolen_bases": [(["career-batting"], "SB")], "walks": [(["career-batting"], "BB")],
        "strikeouts_batting": [(["career-batting"], "SO")],
        "strikeouts": [(["pitching"], "K")], "wins": [(["pitching"], "W")], "saves": [(["pitching"], "SV")],
        "complete_games": [(["expanded-pitching"], "CG")], "shutouts": [(["expanded-pitching"], "SHO")],
    },
}
ESPN_LEAGUE_DESC = {"NFL": "NFL", "NBA": "NBA", "NHL": "NHL", "MLB": "MLB"}


def espn_year(league, season):
    """ESPN keys NBA/NHL seasons by the year they end: '1986-87' -> 1987."""
    start = season_start(season)
    return start + 1 if league in ("NBA", "NHL") else start


def espn_num(text):
    """'5,250' -> 5250.0, '25-72' (made-attempted) -> 25.0."""
    text = str(text).replace(",", "").strip()
    if "-" in text[1:]:
        text = text.split("-")[0]
    return float(text)


def espn_season_value(stats, league, year, picks):
    """Sum the picked columns for one season. None if the player has no row that season."""
    total, found = 0.0, False
    for cats, label in picks:
        for c in stats.get("categories", []):
            if c.get("name", "").startswith("postseason") or (cats and c.get("name") not in cats):
                continue
            labels = c.get("labels", [])
            if label not in labels:
                continue
            col = labels.index(label)
            rows = [r for r in c.get("statistics", []) if r.get("season", {}).get("year") == year]
            if not rows:
                continue
            totals = [r for r in rows if "Totals" in (r.get("teamSlug") or "")]
            use = totals[:1] if totals else rows  # traded: combined row, else add up team rows
            total += sum(espn_num(r["stats"][col]) for r in use)
            found = True
    return total if found else None


def espn_candidates(q):
    if q.get("espnId"):
        return [str(q["espnId"])]
    data = http_get("https://site.web.api.espn.com/apis/search/v2?limit=20&query="
                    + urllib.parse.quote(q["player"]))
    hits = [c for r in data.get("results", []) if r.get("type") == "player" for c in r.get("contents", [])
            if c.get("description") == ESPN_LEAGUE_DESC[q["league"]]]
    exact = [c for c in hits if norm(c.get("displayName", "")) == norm(q["player"])]
    return [c["uid"].split("a:")[-1] for c in (exact or hits)]


def verify_espn(q):
    """Returns (value, url) from ESPN for one question, or raises FetchError."""
    league = q["league"]
    picks = ESPN_STATS[league].get(q["stat"])
    if not picks:
        raise FetchError("no ESPN mapping for %s" % q["stat"])
    year = espn_year(league, q["season"])
    found = []
    for aid in espn_candidates(q):
        try:
            stats = http_get("https://site.web.api.espn.com/apis/common/v3/sports/%s/athletes/%s/stats"
                             % (ESPN_PATHS[league], aid))
        except FetchError:  # search sometimes lists players with no stats page
            continue
        v = espn_season_value(stats, league, year, picks)
        if v is not None:
            found.append((aid, v))
    if not found:
        raise FetchError("ESPN has no %s %s season for %s" % (league, q["season"], q["player"]))
    if len(found) > 1:
        raise FetchError("several ESPN players named %r played in %s; set espnId to one of %s"
                         % (q["player"], q["season"], [f[0] for f in found]))
    aid, v = found[0]
    url = "https://www.espn.com/%s/player/stats/_/id/%s" % (league.lower(), aid)
    return v, url


# ---------------------------------------------------------------------------
# Bank helpers
# ---------------------------------------------------------------------------
def load_bank(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_bank(path, bank):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2, ensure_ascii=False)
        f.write("\n")


def each_question(bank, only_set=None):
    for sport in SPORTS:
        for s in bank.get("sets", {}).get(sport, []):
            if only_set and s.get("id") != only_set:
                continue
            for i, q in enumerate(s.get("questions", [])):
                yield sport, s, i, q


def qname(s, i, q):
    return "%s q%d (%s %s, %s)" % (s.get("id"), i + 1, q.get("player"), q.get("season"), q.get("stat"))


def today():
    return dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_fetch(args):
    bank = load_bank(args.bank)
    ok = fail = skip = 0
    for sport, s, i, q in each_question(bank, args.set):
        if q.get("source") and q["source"].get("value") is not None and not args.refresh:
            skip += 1
            continue
        league = q.get("league")
        spec = STATS.get(league, {}).get(q.get("stat"))
        if not spec:
            print("  SKIP  %s: unknown league/stat" % qname(s, i, q))
            fail += 1
            continue
        try:
            value, pid, src = FETCHERS[league](q, spec)
            q["source"] = {"name": src, "value": value if value % 1 else int(value),
                           "playerId": pid, "fetchedOn": today()}
            if not q.get("playerId"):
                q["playerId"] = pid
            ref = q.setdefault("reference", {"value": None, "url": None, "checkedOn": None})
            ref.setdefault("hint", REF_SITES[league] + urllib.parse.quote_plus(q["player"]))
            print("  OK    %s = %s  [%s]" % (qname(s, i, q), q["source"]["value"], src))
            ok += 1
        except FetchError as e:
            print("  FAIL  %s: %s" % (qname(s, i, q), e))
            fail += 1
        except Exception as e:  # network trouble etc.
            print("  FAIL  %s: %s: %s" % (qname(s, i, q), type(e).__name__, e))
            fail += 1
    save_bank(args.bank, bank)
    print("\nfetched %d, failed %d, already had %d. Saved %s" % (ok, fail, skip, args.bank))


def cmd_verify(args):
    bank = load_bank(args.bank)
    ok = fail = skip = 0
    for sport, s, i, q in each_question(bank, args.set):
        ref = q.get("reference") or {}
        if ref.get("value") is not None and not args.refresh:
            skip += 1  # already checked (by hand or an earlier run)
            continue
        if q.get("league") not in LEAGUES or not STATS[q["league"]].get(q.get("stat")):
            print("  SKIP  %s: unknown league/stat" % qname(s, i, q))
            fail += 1
            continue
        try:
            value, url = verify_espn(q)
            ref = q.setdefault("reference", {})
            ref.update({"value": value if value % 1 else int(value), "url": url,
                        "checkedOn": today(), "by": "ESPN (auto)"})
            ref.setdefault("hint", REF_SITES[q["league"]] + urllib.parse.quote_plus(q["player"]))
            src = (q.get("source") or {}).get("value")
            flag = "" if src is None or float(src) == float(ref["value"]) else "  <-- MISMATCH, source says %s" % src
            print("  OK    %s = %s%s" % (qname(s, i, q), ref["value"], flag))
            ok += 1
        except FetchError as e:
            print("  FAIL  %s: %s (check it by hand)" % (qname(s, i, q), e))
            fail += 1
        except Exception as e:  # network trouble etc.
            print("  FAIL  %s: %s: %s" % (qname(s, i, q), type(e).__name__, e))
            fail += 1
    save_bank(args.bank, bank)
    print("\nverified %d, failed %d, already checked %d. Saved %s. Run check next."
          % (ok, fail, skip, args.bank))


def check_bank(bank, verbose=True):
    """Returns (errors, warnings, pending, ready_set_ids)."""
    errors, warnings, pending = [], [], []
    weights = bank.get("weights", [1, 1, 2, 3, 3])
    ready = set()
    try:
        dt.date.fromisoformat(bank.get("launch", ""))
    except ValueError:
        errors.append("launch must be a date like 2026-10-15")
    seen_ids, seen_q = set(), {}
    for sport in bank.get("sets", {}):
        if sport not in SPORTS:
            errors.append("unknown sport key %r (use %s)" % (sport, ", ".join(SPORTS)))
    for sport in SPORTS:
        for s in bank.get("sets", {}).get(sport, []):
            sid = s.get("id")
            if not sid:
                errors.append("%s set without an id" % sport)
            elif sid in seen_ids:
                errors.append("duplicate set id %s" % sid)
            seen_ids.add(sid)
            qs = s.get("questions", [])
            if len(qs) != len(weights):
                errors.append("%s has %d questions, needs %d" % (sid, len(qs), len(weights)))
            set_ok = len(qs) == len(weights)
            for i, q in enumerate(qs):
                name = qname(s, i, q)
                league = q.get("league")
                if league not in LEAGUES:
                    errors.append("%s: league must be one of %s" % (name, LEAGUES)); set_ok = False; continue
                if sport != "mix" and league != sport.upper():
                    errors.append("%s: %s question in the %s bank" % (name, league, sport)); set_ok = False
                for field in ("player", "season", "stat"):
                    if not q.get(field):
                        errors.append("%s: missing %s" % (name, field)); set_ok = False
                spec = STATS[league].get(q.get("stat"))
                if not spec:
                    errors.append("%s: unknown stat %r (run: stats)" % (name, q.get("stat"))); set_ok = False; continue
                season = str(q.get("season", ""))
                if league in ("NBA", "NHL"):
                    parts = season.split("-")
                    if len(parts) != 2 or len(parts[1]) != 2 or not parts[0].isdigit() \
                            or (int(parts[0]) + 1) % 100 != int(parts[1]):
                        errors.append("%s: %s seasons look like 2015-16" % (name, league)); set_ok = False
                elif not (season.isdigit() and len(season) == 4):
                    errors.append("%s: %s seasons look like 2022" % (name, league)); set_ok = False
                tol = q.get("tolerance") or {}
                pct = tol.get("pct", DEFAULT_PCT[min(i, 4)])
                if not (0 < pct <= 1):
                    errors.append("%s: tolerance pct must be between 0 and 1" % name); set_ok = False
                key = (league, norm(q.get("player", "")), season, q.get("stat"))
                if key in seen_q:
                    warnings.append("%s repeats %s" % (name, seen_q[key]))
                seen_q[key] = name

                src = (q.get("source") or {}).get("value")
                ref = (q.get("reference") or {}).get("value")
                if src is None:
                    pending.append("%s: no source value yet (run fetch)" % name); set_ok = False
                    continue
                if src % 1:
                    errors.append("%s: answer %s is not a whole number; pick a different stat" % (name, src))
                    set_ok = False
                if ref is None:
                    pending.append("%s: source says %s, needs reference check -> %s"
                                   % (name, src, (q.get("reference") or {}).get("hint", "")))
                    set_ok = False
                elif float(ref) != float(src):
                    errors.append("%s: MISMATCH source %s vs reference %s. Find out why before using it"
                                  % (name, src, ref))
                    set_ok = False
                elif not (q.get("reference") or {}).get("url"):
                    warnings.append("%s: add the reference url you checked" % name)
            if set_ok:
                ready.add(sid)
    if verbose:
        for e in errors:
            print("  ERROR    " + e)
        for p in pending:
            print("  PENDING  " + p)
        for w in warnings:
            print("  WARN     " + w)
    return errors, warnings, pending, ready


def cmd_check(args):
    bank = load_bank(args.bank)
    errors, warnings, pending, ready = check_bank(bank)
    total = sum(len(bank.get("sets", {}).get(sp, [])) for sp in SPORTS)
    print("\n%d errors, %d pending, %d warnings. %d of %d sets ready to ship."
          % (len(errors), len(pending), len(warnings), len(ready), total))
    if args.mark:
        for sport in SPORTS:
            for s in bank.get("sets", {}).get(sport, []):
                s["status"] = "ready" if s.get("id") in ready else "draft"
        save_bank(args.bank, bank)
    sys.exit(1 if errors else 0)


# ---------------------------------------------------------------------------
# Daily schedule. A set with a "date" plays on that day and is then used up.
# Hand-made sets (make) get their date from you; every other day is filled with
# a random ready set from the pool (schedule, also run by export).
# ---------------------------------------------------------------------------
TIER_NAMES = ["easy", "easy", "medium", "hard", "hard"]


def dated(bank, sport, day):
    return [s for s in bank.get("sets", {}).get(sport, []) if s.get("date") == day]


def schedule_bank(bank, days):
    """Gives every sport a set for today .. today+days-1. Returns (assigned, missing) lists."""
    _, _, _, ready = check_bank(bank, verbose=False)
    rng = random.Random()
    start = max(dt.date.today(), dt.date.fromisoformat(bank["launch"]))
    assigned, missing = [], []
    for n in range(days):
        day = (start + dt.timedelta(days=n)).isoformat()
        for sport in SPORTS:
            ss = dated(bank, sport, day)
            mine = [s for s in ss if s.get("handmade") and s.get("id") in ready]
            if mine and day > dt.date.today().isoformat():
                for s in ss:  # your verified set wins; a random backup goes back to the pool
                    if not s.get("handmade"):
                        s.pop("date", None)
                        s.pop("scheduledBy", None)
            if any(s.get("id") in ready for s in dated(bank, sport, day)):
                continue  # covered (a hand-made set that isn't verified yet gets a random backup)
            pool = [s for s in bank["sets"].get(sport, []) if not s.get("date") and s.get("id") in ready]
            if not pool:
                missing.append((day, sport))
                continue
            pick = rng.choice(pool)
            pick["date"] = day
            pick["scheduledBy"] = "random"
            assigned.append((day, sport, pick["id"]))
    return assigned, missing


def report_missing(missing):
    if not missing:
        return
    by_sport = {}
    for day, sport in missing:
        by_sport.setdefault(sport, []).append(day)
    print("\nNo set available (pool empty) for:")
    for sport, ds in by_sport.items():
        print("  %-4s %d day(s) from %s. Add sets with make or generate." % (sport, len(ds), ds[0]))


def cmd_schedule(args):
    bank = load_bank(args.bank)
    assigned, missing = schedule_bank(bank, args.days)
    save_bank(args.bank, bank)
    for day, sport, sid in assigned:
        print("  %s  %-4s %s (random)" % (day, sport, sid))
    report_missing(missing)
    _, _, _, ready = check_bank(bank, verbose=False)
    start = dt.date.today()
    print("\nNext %d days (the set that plays):" % args.days)
    for n in range(args.days):
        day = (start + dt.timedelta(days=n)).isoformat()
        row = []
        for sport in SPORTS:
            ok = sorted((s for s in dated(bank, sport, day) if s.get("id") in ready),
                        key=lambda s: not s.get("handmade"))
            row.append("%s:%s" % (sport, (ok[0]["id"] + ("*" if ok[0].get("handmade") else "")) if ok else "-"))
            for s in dated(bank, sport, day):
                if s.get("handmade") and s.get("id") not in ready:
                    print("  WARNING  %s %s: your set %s is not verified yet (run check)" % (day, sport, s["id"]))
        print("  %s  %s" % (day, "  ".join(row)))
    print("(* = hand-made, - = no set)")


def ask(prompt, check=None, default=None):
    while True:
        try:
            ans = input(prompt).strip()
        except EOFError:
            sys.exit("\nStopped: no more input.")
        if not ans and default is not None:
            return default
        err = check(ans) if check else (None if ans else "required")
        if not err:
            return ans
        print("    " + err)


def stat_key(league, text):
    t = text.strip().lower()
    for key, spec in STATS[league].items():
        if t in (key, spec["label"].lower()):
            return key
    return None


def season_error(league, text):
    if league in ("NBA", "NHL"):
        p = text.split("-")
        if len(p) == 2 and p[0].isdigit() and len(p[1]) == 2 and (int(p[0]) + 1) % 100 == int(p[1]):
            return None
        return "%s seasons look like 2015-16" % league
    return None if text.isdigit() and len(text) == 4 else "%s seasons look like 2022" % league


def used_questions(bank, skip_set=None):
    """(league, player, season, stat) -> where it's used, for every question in the bank."""
    seen = {}
    for _, s, i, q in each_question(bank):
        if s.get("id") != skip_set:
            seen[(q.get("league"), norm(q.get("player", "")), str(q.get("season")), q.get("stat"))] = qname(s, i, q)
    return seen


def ask_question(bank, sport, i, used):
    """Prompts for question i of a set. Refuses one that's already in the bank."""
    print("Question %d (%s, worth %dx)" % (i + 1, TIER_NAMES[i], bank.get("weights", [1, 1, 2, 3, 3])[i]))
    while True:
        if sport == "mix":
            league = ask("  League (NFL/NBA/NHL/MLB): ",
                         lambda a: None if a.upper() in LEAGUES else "one of NFL, NBA, NHL, MLB").upper()
        else:
            league = sport.upper()
        player = ask("  Player: ")
        season = ask("  Season: ", lambda a: season_error(league, a))
        stat = stat_key(league, ask("  Stat: ", lambda a: None if stat_key(league, a) else
                                    "unknown %s stat. Options: %s" % (league, ", ".join(STATS[league]))))
        key = (league, norm(player), season, stat)
        if key in used:
            print("    Already used in %s. Pick a different question.\n" % used[key])
            continue
        fact = ask("  Fun fact (optional, Enter to skip): ", default="")
        print()
        return key, {"league": league, "player": player, "season": season, "stat": stat,
                     "playerId": None, "tolerance": None, "source": None,
                     "reference": {"value": None, "url": None, "checkedOn": None}, "fact": fact or None}


def cmd_make(args):
    bank = load_bank(args.bank)
    sport = args.sport.lower()
    if sport not in SPORTS:
        sys.exit("sport must be one of " + ", ".join(SPORTS))
    day = args.date or (dt.date.today() + dt.timedelta(days=1)).isoformat()
    try:
        when = dt.date.fromisoformat(day)
    except ValueError:
        sys.exit("date must look like 2026-10-16")
    if when <= dt.date.today():
        sys.exit("Pick a future date: today's set may already have been played.")
    taken = dated(bank, sport, day)
    mine = [s for s in taken if s.get("handmade")]
    if mine and not args.replace:
        sys.exit("%s already has your hand-made set %s for %s. Add --replace to make a new one."
                 % (sport, mine[0]["id"], day))

    print("Making the %s set for %s. Five questions, easiest first." % (sport.upper() if sport != "mix" else "Mixed", day))
    print("Stats: type a key or its label (list them with: python spot_on_bank.py stats).\n")
    used = used_questions(bank)
    qs = []
    for i in range(len(TIER_NAMES)):
        key, q = ask_question(bank, sport, i, used)
        used[key] = "this set"
        qs.append(q)

    sets = bank["sets"].setdefault(sport, [])
    nums = [int(s["id"].split("-")[-1]) for s in sets if s.get("id", "").split("-")[-1].isdigit()]
    sid = "%s-%04d" % (sport, max(nums + [0]) + 1)
    for s in taken:  # the new set takes the day; anything there before goes back to the pool or away
        if s.get("handmade"):
            sets.remove(s)
            print("Replaced your earlier set %s." % s["id"])
        else:
            s.pop("date", None)
            s.pop("scheduledBy", None)
            print("%s goes back to the random pool." % s["id"])
    sets.append({"id": sid, "status": "draft", "handmade": True, "date": day, "created": today(), "questions": qs})
    save_bank(args.bank, bank)

    print("Saved %s. Checking the answers...\n" % sid)
    while True:
        sub = argparse.Namespace(bank=args.bank, set=sid, refresh=False)
        cmd_fetch(sub)
        cmd_verify(sub)
        bank = load_bank(args.bank)
        errors, _, pending, ready = check_bank(bank, verbose=False)
        if sid in ready:
            print("\n%s is verified and will play on %s. Run export to publish it." % (sid, day))
            return
        issues = [m for m in errors + pending if m.startswith(sid + " q")]
        print()
        for m in issues:
            print("  " + m)
        bad = sorted({int(m[len(sid) + 2:].split(" ")[0]) - 1 for m in issues})
        if not bad or ask("\nRe-enter question %s now? (y/n): " % ", ".join(str(b + 1) for b in bad),
                          lambda a: None if a.lower() in ("y", "n") else "y or n").lower() == "n":
            print("%s is NOT ready yet. Run make again with --replace, or fix questions.json and run fetch/verify."
                  "\nIf it isn't fixed before %s, that day gets no %s set." % (sid, day, sport))
            return
        s = next(x for x in bank["sets"][sport] if x["id"] == sid)
        used = used_questions(bank, skip_set=sid)
        for j, q in enumerate(s["questions"]):
            if j not in bad:
                used[(q["league"], norm(q["player"]), str(q["season"]), q["stat"])] = "this set"
        print()
        for b in bad:
            key, s["questions"][b] = ask_question(bank, sport, b, used)
            used[key] = "this set"
        save_bank(args.bank, bank)
        print("Checking again...\n")


def cmd_export(args):
    bank = load_bank(args.bank)
    errors, _, _, _ = check_bank(bank, verbose=False)
    if errors:  # sets with errors aren't ready, so they're never scheduled or exported
        print("Note: %d errors in the bank (run check). Those sets are left out." % len(errors))
    _, missing = schedule_bank(bank, args.days)
    save_bank(args.bank, bank)
    _, _, _, ready = check_bank(bank, verbose=False)
    weights = bank.get("weights", [1, 1, 2, 3, 3])
    since = (dt.date.today() - dt.timedelta(days=1)).isoformat()  # players a timezone behind
    out = {"launch": bank["launch"], "days": {}}
    not_ready = []
    for sport in SPORTS:
        for s in bank.get("sets", {}).get(sport, []):
            day = s.get("date")
            if not day or day < since:
                continue
            if s.get("id") not in ready:
                not_ready.append((day, sport, s["id"]))
                continue
            if sport in out["days"].get(day, {}) and not s.get("handmade"):
                continue  # today only: your set and a random one share the day; yours plays
            game = []
            for i, q in enumerate(s["questions"]):
                spec = STATS[q["league"]][q["stat"]]
                tol = q.get("tolerance") or {}
                game.append({
                    "lg": q["league"],
                    "player": q["player"],
                    "season": str(q["season"]),
                    "stat": spec["label"],
                    "actual": int(q["source"]["value"]),
                    "pct": tol.get("pct", DEFAULT_PCT[min(i, 4)]),
                    "floor": tol.get("floor", spec["floor"]),
                    "w": weights[i],
                    "fact": q.get("fact"),
                })
            out["days"].setdefault(day, {})[sport] = {"id": s["id"], "questions": game}
    out["days"] = dict(sorted(out["days"].items()))
    js = "// Generated by spot_on_bank.py on %s. Do not edit by hand.\nwindow.SPOT_ON_BANK = %s;\n" % (
        today(), json.dumps(out, indent=2, ensure_ascii=False))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(js)
    print("Wrote %s with %d days (%s to %s)." % (args.out, len(out["days"]),
          min(out["days"], default="-"), max(out["days"], default="-")))
    for day, sport, sid in not_ready:
        print("  WARNING  %s %s: your set %s is not verified yet; a random set plays instead unless you fix it" % (day, sport, sid))
    report_missing(missing)


# ---------------------------------------------------------------------------
# Auto-generate sets from season leaderboards (source #1 data), every league.
# Difficulty comes from how famous the number is:
#   easy (1x)  : league leader or runner-up in headline stats, recent seasons
#   medium (2x): top 3 in second-tier stats
#   hard (3x)  : top 5 in less-watched stats, or headline stats from older seasons
# Each new question then goes through fetch + verify; ones that fail are swapped out.
# ---------------------------------------------------------------------------
PLAN = ["easy", "easy", "medium", "hard", "hard"]
GEN = {
    "NFL": {"first": 1999, "tiers": [
        {"name": "easy", "stats": ["passing_yards", "rushing_yards", "receiving_yards", "passing_tds"],
         "since": 2008, "ranks": 2},
        {"name": "medium", "stats": ["receiving_tds", "rushing_tds", "receptions", "scrimmage_yards", "total_tds"],
         "since": 2003, "ranks": 3},
        {"name": "hard", "stats": ["def_interceptions", "interceptions_thrown", "carries", "completions"],
         "ranks": 5, "old": ["passing_yards", "rushing_yards", "receiving_yards"], "old_until": 2007},
    ], "min": {  # keeps answers big enough to be interesting
        "passing_yards": 3500, "rushing_yards": 1000, "receiving_yards": 1000, "passing_tds": 25,
        "receiving_tds": 9, "rushing_tds": 9, "total_tds": 12, "receptions": 80, "scrimmage_yards": 1400,
        "def_interceptions": 5, "interceptions_thrown": 15, "carries": 250, "completions": 300,
    }},
    "MLB": {"first": 1960, "tiers": [
        {"name": "easy", "stats": ["home_runs", "hits", "strikeouts"], "since": 1990, "ranks": 2},
        {"name": "medium", "stats": ["rbi", "runs", "wins", "saves", "doubles", "stolen_bases"],
         "since": 1980, "ranks": 3},
        {"name": "hard", "stats": ["walks", "triples", "strikeouts_batting", "complete_games", "shutouts"],
         "ranks": 5, "old": ["home_runs", "hits", "strikeouts", "stolen_bases"], "old_until": 1989},
    ], "min": {
        "home_runs": 35, "hits": 190, "strikeouts": 220, "rbi": 110, "runs": 110, "wins": 18, "saves": 35,
        "doubles": 40, "stolen_bases": 40, "walks": 100, "triples": 10, "strikeouts_batting": 150,
        "complete_games": 8, "shutouts": 4,
    }},
    "NHL": {"first": 1985, "tiers": [
        {"name": "easy", "stats": ["goals", "points"], "since": 2000, "ranks": 2},
        {"name": "medium", "stats": ["assists", "ppg"], "since": 1990, "ranks": 3},
        {"name": "hard", "stats": ["pim", "gwg", "shots", "goalie_wins", "shutouts"],
         "ranks": 5, "old": ["goals", "points", "assists"], "old_until": 1999},
    ], "min": {
        "goals": 40, "points": 90, "assists": 60, "ppg": 15, "pim": 200, "gwg": 9, "shots": 300,
        "goalie_wins": 35, "shutouts": 7,
    }},
    "NBA": {"first": 1996, "tiers": [  # stats.nba.com season totals start in 1996-97
        {"name": "easy", "stats": ["points", "rebounds", "assists", "threes_made"], "since": 2005, "ranks": 2},
        {"name": "medium", "stats": ["steals", "blocks", "triple_doubles", "double_doubles"], "ranks": 3},
        {"name": "hard", "stats": ["free_throws_made"],
         "ranks": 5, "old": ["points", "rebounds", "assists", "threes_made"], "old_until": 2004},
    ], "min": {
        "points": 1800, "rebounds": 800, "assists": 550, "threes_made": 180, "steals": 140, "blocks": 150,
        "triple_doubles": 6, "double_doubles": 45, "free_throws_made": 500,
    }},
}
NHL_LEADER_FIELDS = {
    "skater": {"goals": "goals", "assists": "assists", "points": "points", "pim": "penaltyMinutes",
               "games": "gamesPlayed", "ppg": "ppGoals", "gwg": "gameWinningGoals", "shots": "shots"},
    "goalie": {"goalie_wins": "wins", "shutouts": "shutouts"},
}


def last_complete(league):
    """Start year of the last finished regular season."""
    t = dt.date.today()
    if league == "NFL":
        return t.year - 1 if t.month >= 3 else t.year - 2
    if league == "MLB":
        return t.year if t.month >= 10 else t.year - 1
    return t.year - 1 if t.month >= 7 else t.year - 2  # NBA / NHL end in April-June


def season_label(league, year):
    return "%d-%02d" % (year, (year + 1) % 100) if league in ("NBA", "NHL") else str(year)


def season_table(league, year):
    """Every player's season: [{player, playerId, <stat key>: value}]."""
    if league == "NFL":
        out = []
        for r in nfl_rows(year):
            d = {"player": r["player_display_name"], "playerId": r["player_id"]}
            for key, spec in STATS["NFL"].items():
                d[key] = sum(float(r.get(c) or 0) for c in spec["cols"])
            out.append(d)
        return out
    if league == "MLB":
        rows = {}
        for group in ("hitting", "pitching"):
            data = http_get("https://statsapi.mlb.com/api/v1/stats?stats=season&group=%s&season=%d"
                            "&sportId=1&playerPool=All&limit=5000" % (group, year), cache_days=365)
            for sp in (data.get("stats") or [{}])[0].get("splits", []):
                pid = str(sp["player"]["id"])
                d = rows.setdefault(pid, {"player": sp["player"]["fullName"], "playerId": pid})
                for key, spec in STATS["MLB"].items():
                    if spec["group"] == group:
                        d[key] = d.get(key, 0) + float(sp["stat"].get(spec["field"]) or 0)
        return list(rows.values())
    if league == "NHL":
        rows = {}
        for kind in ("skater", "goalie"):
            url = ("https://api.nhle.com/stats/rest/en/%s/summary?limit=-1&cayenneExp=" % kind
                   + urllib.parse.quote("seasonId=%d%d and gameTypeId=2" % (year, year + 1)))
            for r in http_get(url, cache_days=365).get("data", []):
                pid = str(r["playerId"])
                d = rows.setdefault(pid, {"player": r.get("skaterFullName") or r.get("goalieFullName"),
                                          "playerId": pid})
                for key, field in NHL_LEADER_FIELDS[kind].items():
                    if r.get(field) is not None:
                        d[key] = float(r[field])
        return list(rows.values())
    # NBA: season totals from stats.nba.com, cached because the site is slow and touchy
    label = season_label("NBA", year)
    path = os.path.join(CACHE, "nba_dash_%s.json" % label)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            recs = json.load(f)
    else:
        nba_api()
        from nba_api.stats.endpoints import leaguedashplayerstats
        time.sleep(0.8)
        df = leaguedashplayerstats.LeagueDashPlayerStats(
            season=label, per_mode_detailed="Totals", season_type_all_star="Regular Season",
            timeout=60).get_data_frames()[0]
        recs = json.loads(df.to_json(orient="records"))
        if not recs:
            raise FetchError("stats.nba.com has no season totals for %s" % label)
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recs, f)
    out = []
    for r in recs:
        d = {"player": r["PLAYER_NAME"], "playerId": str(r["PLAYER_ID"])}
        for key, spec in STATS["NBA"].items():
            col = spec.get("col") or {3: "TD3", 2: "DD2"}[spec["gamelog"]]
            if r.get(col) is not None:
                d[key] = float(r[col])
        out.append(d)
    return out


def candidates(league, first, last):
    """Every question worth asking, by tier: {tier: [{league, player, playerId, season, stat, value, rank, tied}]}."""
    cfg = GEN[league]
    out = {t["name"]: [] for t in cfg["tiers"]}
    for year in range(first, last + 1):
        try:
            rows = season_table(league, year)
        except Exception as e:  # missing season, network trouble: skip that year
            print("  skipped %s %s: %s" % (league, season_label(league, year), e))
            continue
        for tier in cfg["tiers"]:
            if year < tier.get("since", 0):
                continue
            stats = tier["stats"] + (tier.get("old", []) if year <= tier.get("old_until", -1) else [])
            for stat in stats:
                ranked = sorted((r for r in rows if r.get(stat)), key=lambda r: -r[stat])
                values = [r[stat] for r in ranked]
                for r in ranked:
                    v = r[stat]
                    rank = 1 + values.index(v)  # ties share a rank
                    if rank > tier["ranks"]:
                        break
                    if v < cfg["min"].get(stat, 0) or v % 1:
                        continue
                    out[tier["name"]].append({
                        "league": league, "player": r["player"], "playerId": r["playerId"],
                        "season": season_label(league, year), "stat": stat, "value": int(v),
                        "rank": rank, "tied": values.count(v) > 1,
                    })
    return out


def lead_fact(c):
    """Only league-leading seasons get an automatic fact; anything else is left for a human."""
    if c["rank"] != 1:
        return None
    return ("Tied for the %s lead in %s." if c["tied"] else "Led the %s in %s.") % (c["league"], c["season"])


def cand_key(c):
    return (c["league"], norm(c["player"]), str(c["season"]), c["stat"])


def pick_question(pool, picked, used, mix):
    """Takes the first candidate that fits next to the questions already picked."""
    for c in pool:
        if cand_key(c) in used:
            continue
        if any(p["league"] == c["league"] and (str(p["playerId"]) == str(c["playerId"]) or p["stat"] == c["stat"])
               for p in picked):
            continue
        if mix and sum(1 for p in picked if p["league"] == c["league"]) >= 2:
            continue
        pool.remove(c)
        return c
    return None


def gen_question(c):
    return {
        "league": c["league"], "player": c["player"], "season": c["season"], "stat": c["stat"],
        "playerId": c["playerId"], "tolerance": None, "source": None,
        "reference": {"value": None, "url": None, "checkedOn": None,
                      "hint": REF_SITES[c["league"]] + urllib.parse.quote_plus(c["player"])},
        "fact": lead_fact(c),
    }


def verify_generated(path, sport, ids, pools, used, rounds=4):
    """fetch + verify the new sets, swap out failing questions, drop sets that never pass."""
    todo = list(ids)
    for n in range(rounds):
        if not todo:
            break
        print("  checking %d %s sets (round %d)..." % (len(todo), sport, n + 1))
        with contextlib.redirect_stdout(io.StringIO()):
            for sid in todo:
                sub = argparse.Namespace(bank=path, set=sid, refresh=False)
                cmd_fetch(sub)
                cmd_verify(sub)
        bank = load_bank(path)
        errors, _, pending, ready = check_bank(bank, verbose=False)
        todo = [sid for sid in todo if sid not in ready]
        sets = {s["id"]: s for s in bank["sets"][sport]}
        for sid in todo:
            s = sets[sid]
            bad = sorted({int(m[len(sid) + 2:].split(" ")[0]) - 1
                          for m in errors + pending if m.startswith(sid + " q")})
            for b in bad:
                others = [{"league": q["league"], "playerId": q.get("playerId"), "stat": q["stat"]}
                          for j, q in enumerate(s["questions"]) if j != b]
                c = pick_question(pools[PLAN[b]], others, used, sport == "mix")
                if c:
                    s["questions"][b] = gen_question(c)
                    used[cand_key(c)] = sid
        save_bank(path, bank)
    bank = load_bank(path)
    _, _, _, ready = check_bank(bank, verbose=False)
    dropped = [sid for sid in ids if sid not in ready]
    bank["sets"][sport] = [s for s in bank["sets"][sport] if s["id"] not in dropped]
    save_bank(path, bank)
    if dropped:
        print("  dropped %d sets that couldn't be fully verified: %s" % (len(dropped), ", ".join(dropped)))
    return len(ids) - len(dropped)


def cmd_generate(args):
    choice = args.league.lower()
    sports = SPORTS if choice == "all" else [choice]
    if any(s not in SPORTS for s in sports):
        sys.exit("--league must be one of %s, or all" % ", ".join(SPORTS))
    leagues = LEAGUES if "mix" in sports else [s.upper() for s in sports]
    rnd = random.Random(args.seed)
    pools = {}
    for league in leagues:
        cfg = GEN[league]
        first, last = max(cfg["first"], args.since or 0), min(args.to or 9999, last_complete(league))
        print("Reading %s seasons %s to %s..." % (league, season_label(league, first), season_label(league, last)))
        pools[league] = candidates(league, first, last)
        for p in pools[league].values():
            rnd.shuffle(p)

    for sport in sports:
        bank = load_bank(args.bank)
        used = used_questions(bank)
        if sport == "mix":  # every league's candidates, max 2 questions per league in a set
            tier_pools = {t: [c for lg in LEAGUES for c in pools[lg][t]] for t in set(PLAN)}
            for p in tier_pools.values():
                rnd.shuffle(p)
        else:
            tier_pools = pools[sport.upper()]
        sets = bank["sets"].setdefault(sport, [])
        nums = [int(s["id"].split("-")[-1]) for s in sets if s.get("id", "").split("-")[-1].isdigit()]
        next_num = max(nums + [0]) + 1
        new_ids = []
        for _ in range(args.count):
            picked = []
            for tier in PLAN:
                c = pick_question(tier_pools[tier], picked, used, sport == "mix")
                if not c:
                    break
                picked.append(c)
            if len(picked) < len(PLAN):
                print("  ran out of fresh %s questions after %d sets" % (sport, len(new_ids)))
                break
            sid = "%s-%04d" % (sport, next_num)
            next_num += 1
            for c in picked:
                used[cand_key(c)] = sid
            sets.append({"id": sid, "status": "draft", "generated": today(),
                         "questions": [gen_question(c) for c in picked]})
            new_ids.append(sid)
        save_bank(args.bank, bank)
        if args.no_check:
            print("Added %d %s sets (not checked yet: run fetch and verify)." % (len(new_ids), sport))
            continue
        kept = verify_generated(args.bank, sport, new_ids, tier_pools, used)
        print("Added %d verified %s sets." % (kept, sport))


def cmd_find(args):
    league = args.league.upper()
    name = args.name
    if league == "NFL":
        season = args.season or 2023
        rows = [r for r in nfl_rows(int(season)) if norm(name) in norm(r["player_display_name"])]
        for r in rows:
            print("  %s  %s  %s %s" % (r["player_id"], r["player_display_name"], r["position"], r["recent_team"]))
    elif league == "NBA":
        players, _, _ = nba_api()
        for p in players.get_players():
            if norm(name) in norm(p["full_name"]):
                print("  %s  %s  active=%s" % (p["id"], p["full_name"], p["is_active"]))
    elif league == "NHL":
        for p in nhl_search(name):
            print("  %s  %s  %s" % (p.get("playerId"), p.get("name"), p.get("lastTeamAbbrev") or ""))
    elif league == "MLB":
        data = http_get("https://statsapi.mlb.com/api/v1/people/search?names=" + urllib.parse.quote(name))
        for p in data.get("people", []):
            print("  %s  %s  debut %s" % (p.get("id"), p.get("fullName"), p.get("mlbDebutDate", "?")))
    else:
        sys.exit("league must be NFL, NBA, NHL or MLB")


def cmd_stats(args):
    for league in LEAGUES:
        print(league)
        for key, spec in STATS[league].items():
            print("  %-22s %s" % (key, spec["label"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spot On Daily question bank tool")
    ap.add_argument("--bank", default=DEFAULT_BANK, help="path to questions.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="pull answers from the data sources")
    f.add_argument("--set", help="only this set id")
    f.add_argument("--refresh", action="store_true", help="re-fetch values already filled in")
    f.set_defaults(func=cmd_fetch)

    v = sub.add_parser("verify", help="fill in the reference check automatically from ESPN (source #2)")
    v.add_argument("--set", help="only this set id")
    v.add_argument("--refresh", action="store_true", help="re-check values already filled in")
    v.set_defaults(func=cmd_verify)

    c = sub.add_parser("check",help="validate the bank and compare sources")
    c.add_argument("--mark", action="store_true", help="write status ready/draft onto each set")
    c.set_defaults(func=cmd_check)

    e = sub.add_parser("export", help="schedule the coming days and write bank.js for the game")
    e.add_argument("--out", default=os.path.join(HERE, "bank.js"))
    e.add_argument("--days", type=int, default=14, help="how many days ahead to schedule (default 14)")
    e.set_defaults(func=cmd_export)

    m = sub.add_parser("make", help="hand-make the set for a day (asks for 5 questions)")
    m.add_argument("--sport", required=True, help="nfl, nba, nhl, mlb or mix")
    m.add_argument("--date", help="day it plays, like 2026-10-16 (default: tomorrow)")
    m.add_argument("--replace", action="store_true", help="replace your earlier hand-made set for that day")
    m.set_defaults(func=cmd_make)

    sc = sub.add_parser("schedule", help="show the coming days; fill days without a set at random")
    sc.add_argument("--days", type=int, default=14)
    sc.set_defaults(func=cmd_schedule)

    g = sub.add_parser("generate", help="auto-build verified sets from season leaderboards")
    g.add_argument("--league", default="nfl", help="nfl, nba, nhl, mlb, mix, or all")
    g.add_argument("--count", type=int, default=20, help="how many sets to add per sport")
    g.add_argument("--since", type=int, help="earliest season (start year) to use")
    g.add_argument("--to", type=int, help="latest season (default: last completed season)")
    g.add_argument("--seed", type=int, help="fix the shuffle (default: different every run)")
    g.add_argument("--no-check", action="store_true", help="skip the automatic fetch + verify")
    g.set_defaults(func=cmd_generate)

    fd = sub.add_parser("find", help="look up player IDs by name")
    fd.add_argument("name")
    fd.add_argument("--league", required=True)
    fd.add_argument("--season", help="NFL only: season to search (default 2023)")
    fd.set_defaults(func=cmd_find)

    st = sub.add_parser("stats", help="list stat keys per league")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
