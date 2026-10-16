#!/usr/bin/env python3
"""
Builds site/, the folder you upload to Netlify.

  python bank/spot_on_bank.py export   # schedule the coming days, write bank/bank.js
  python publish.py                    # copy the game + bank.js into site/
  then drag the site folder onto Netlify (Deploys tab), or: netlify deploy --prod --dir site

site/ is rebuilt from scratch every time; never edit it by hand.
"""

import datetime as dt
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.join(HERE, "game", "index.html")
BANK = os.path.join(HERE, "bank", "bank.js")
SITE = os.path.join(HERE, "site")
SPORTS = ["nfl", "nba", "nhl", "mlb", "mix"]

# bank.js changes with every export, so browsers should always check for a new copy.
HEADERS = """/bank.js
  Cache-Control: no-cache
/
  Cache-Control: no-cache
"""


# Streaks are saved per web address, so send everyone on the old Netlify address to the real domain.
REDIRECTS = """https://spotondaily.netlify.app/* https://spotondaily.com/:splat 301!
"""


def main():
    if not os.path.exists(BANK):
        sys.exit("No bank/bank.js yet. Run: python bank/spot_on_bank.py export")
    with open(BANK, encoding="utf-8") as f:
        js = f.read()
    days = json.loads(js[js.index("{"):js.rindex("}") + 1])["days"]

    with open(GAME, encoding="utf-8") as f:
        html = f.read()
    tag = '<script src="../bank/bank.js"></script>'
    if tag not in html:
        sys.exit("game/index.html no longer loads ../bank/bank.js; update publish.py to match.")
    html = html.replace(tag, '<script src="bank.js"></script>')

    # Empty site/ rather than deleting it: Windows refuses to remove a folder that Explorer,
    # OneDrive or an upload dialog has open.
    os.makedirs(SITE, exist_ok=True)
    for name in os.listdir(SITE):
        path = os.path.join(SITE, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    shutil.copyfile(BANK, os.path.join(SITE, "bank.js"))
    with open(os.path.join(SITE, "_headers"), "w", encoding="utf-8") as f:
        f.write(HEADERS)
    with open(os.path.join(SITE, "_redirects"), "w", encoding="utf-8") as f:
        f.write(REDIRECTS)

    # Report how far the schedule reaches, so the site never runs dry.
    today = dt.date.today()
    print("Built %s" % SITE)
    gaps, last_full, unbroken = [], None, True
    for n in range(14):
        day = (today + dt.timedelta(days=n)).isoformat()
        missing = [s for s in SPORTS if s not in days.get(day, {})]
        if missing:
            gaps.append("%s: no %s" % (day, ", ".join(missing)))
            unbroken = False
        elif unbroken:
            last_full = day
    if last_full:
        print("Every sport has a set through %s. Re-run export + publish before then." % last_full)
    else:
        print("  WARNING  today is missing sets")
    for g in gaps:
        print("  WARNING  " + g)
    print("Upload the site folder to Netlify.")


if __name__ == "__main__":
    main()
