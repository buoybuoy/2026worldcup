#!/usr/bin/env python3
"""Daily KNOCKOUT-stage leaderboard recap generator.

The group-stage sibling is recap.py. Once the group stage is over the bracket
competition takes over: everyone's KNOCKOUT_PREDS picks (winners by round) are
scored with the app's exact KO_SCORING (R32 25 / R16 50 / QF 100 / SF 200 /
Final 400), membership-style — a pick earns its round's points if that team is
among the round's actual winners.

This pulls the day's knockout results live from ESPN (so it works even if the
committed KO_RESULTS in index.html is stale), classifies each match into a round
by date, recomputes the knockout leaderboard + the day's movement, flags the
biggest upsets (low pick %) and bracket-busters (eliminated teams people had
going deep), surfaces the next slate, and prints a Discord draft to polish.

Usage:
    python3 scripts/ko_recap.py            # recap for today (PT)
    python3 scripts/ko_recap.py 2026-06-29 # recap for a specific day
"""

import re
import sys
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import Counter

PT = ZoneInfo('America/Los_Angeles')
HERE = __file__.rsplit('/', 2)[0]
INDEX = HERE + '/index.html'

# ESPN display name -> app name (same set recap.py uses)
NAME_MAP = {
    'Czech Republic': 'Czechia', "Côte d'Ivoire": 'Ivory Coast', 'Turkey': 'Türkiye',
    'Bosnia-Herzegovina': 'Bosnia and Herzegovina', 'Cabo Verde': 'Cape Verde',
    'United States': 'USA', 'Korea Republic': 'South Korea', 'Congo DR': 'DR Congo',
    'Democratic Republic of Congo': 'DR Congo', 'Curacao': 'Curaçao', 'IR Iran': 'Iran',
}
SHORT = {'光榮的人民主席 Chairman Lau': 'Chairman Lau', '대한민국 남바원': '남바원'}
def short(n): return SHORT.get(n, n)

# round key in KNOCKOUT_PREDS  <->  KO_SCORING key
ROUND_PRED = {'R32': 'r32', 'R16': 'r16', 'QF': 'qf', 'SF': 'sf', 'F': 'final'}
ROUND_LABEL = {'R32': 'Round of 32', 'R16': 'Round of 16', 'QF': 'Quarterfinal',
               'SF': 'Semifinal', 'F': 'Final'}


def load():
    """Parse the bracket picks, scoring, and knockout schedule from index.html."""
    html = open(INDEX, encoding='utf-8').read()

    def obj(name):
        # literals hold no inner ';', so non-greedy to the first ';' is safe
        return re.search(r'const ' + name + r'\s*=\s*(.*?);', html, re.S).group(1)

    # KNOCKOUT_PREDS: quote the round keys, swap quote style, drop trailing comma.
    kp = re.search(r'const KNOCKOUT_PREDS\s*=\s*\{(.*?)\n\};', html, re.S).group(1)
    s = ('{' + kp.rstrip().rstrip(',') + '}').replace("'", '"')
    s = re.sub(r'([{,])\s*(r32|r16|qf|sf|final)\s*:', r'\1"\2":', s)
    KP = json.loads(s)

    sc = obj('KO_SCORING')
    SC = json.loads(re.sub(r'([{,])\s*(R32|R16|QF|SF|F)\s*:', r'\1"\2":', sc))

    # Round date-sets: which calendar dates belong to which round. The rounds
    # never overlap, so a completed match's date uniquely identifies its round.
    dates = {'R32': set(), 'R16': set(), 'QF': set(), 'SF': set(), 'F': set(), '3RD': set()}
    for d in re.findall(r"date:'([0-9-]+)'", obj('KNOCKOUT_R32')):
        dates['R32'].add(d)
    later = obj('KNOCKOUT_LATER')
    for rnd in ('R16', 'QF', 'SF', 'F'):
        # Match to the array-closing ']' on its own line (entries hold inline
        # feeder arrays like f:[74,77]); \b so 'F' doesn't match inside 'SF'.
        block = re.search(r'\b' + rnd + r':\[(.*?)\n\s*\]', later, re.S)
        if block:
            for d in re.findall(r"date:'([0-9-]+)'", block.group(1)):
                dates[rnd].add(d)
    third = re.search(r'THIRD:\{(.*?)\}', later, re.S)
    if third:
        for d in re.findall(r"date:'([0-9-]+)'", third.group(1)):
            dates['3RD'].add(d)

    # Cash prize-pool roster (for the overall Top 3).
    pp = re.search(r'const PRIZE_POOL\s*=\s*\[(.*?)\];', html, re.S).group(1)
    PP = json.loads('[' + pp.replace("'", '"') + ']')
    return KP, SC, dates, PP


def group_totals(recap_date):
    """Group-stage total per person (group finishes + 3rd-place advancers),
    reusing recap.py's exact scoring. Returns {name: pts}; {} if unavailable."""
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location('recap', HERE + '/scripts/recap.py')
        recap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recap)
        G, M, SCH, MT, CONF, FP, TP = recap.load()
        scores = recap.fetch_live(G, M, CONF)
        _st, _pl, advancing, _gp, total = recap.make_scorers(G, M, FP, TP)
        end = {k: v for k, v in scores.items() if SCH.get(k, '9999') <= recap_date}
        adv = advancing(end)
        return {n: total(n, end, adv)[0] for n in FP}
    except Exception as e:
        print(f'  (group totals unavailable: {e})')
        return {}


def round_for_date(dates, d):
    for rnd, ds in dates.items():
        if d in ds:
            return rnd
    return None


def _get_json(url, tries=3):
    """GET with a couple of retries — the ESPN scoreboard API intermittently
    times out, and a swallowed failure would silently zero out the recap."""
    last = None
    for _ in range(tries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=20))
        except Exception as e:
            last = e
    raise last


def fetch_ko(dates):
    """All completed knockout matches from ESPN, keyed by date.

    Returns {date: [(round, winner, loser, home, away, 'hs-as'), ...]} plus the
    set of all (date -> [(round, home, away, played?)]) for scheduling 'next up'.
    """
    played, fixtures = {}, {}
    all_dates = sorted(set().union(*dates.values()))
    for d in all_dates:
        url = ('https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/'
               'scoreboard?dates=' + d.replace('-', ''))
        try:
            data = _get_json(url)
        except Exception as e:
            print(f'  (warning: ESPN fetch failed for {d}: {e})', file=sys.stderr)
            continue
        rnd = round_for_date(dates, d)
        for ev in data.get('events', []):
            c = ev['competitions'][0]
            comp = c['competitors']
            try:
                hc = next(x for x in comp if x['homeAway'] == 'home')
                ac = next(x for x in comp if x['homeAway'] == 'away')
            except StopIteration:
                continue
            hn = NAME_MAP.get(hc['team']['displayName'], hc['team']['displayName'])
            an = NAME_MAP.get(ac['team']['displayName'], ac['team']['displayName'])
            done = c['status']['type'].get('completed')
            fixtures.setdefault(d, []).append((rnd, hn, an, bool(done)))
            if not done:
                continue
            hs, as_ = int(round(float(hc.get('score', 0)))), int(round(float(ac.get('score', 0))))
            win = hn if hc.get('winner') else (an if ac.get('winner') else None)
            if not win:
                continue
            lose = an if win == hn else hn
            played.setdefault(d, []).append((rnd, win, lose, hn, an, f'{hs}-{as_}'))
    return played, fixtures


def winners_through(played, upto):
    """Cumulative {round: set(winners)} for all matches dated <= upto."""
    res = {'R32': set(), 'R16': set(), 'QF': set(), 'SF': set(), 'F': set()}
    for d, games in played.items():
        if d > upto:
            continue
        for rnd, win, _lose, *_ in games:
            if rnd in res:
                res[rnd].add(win)
    return res


def score(name, KP, SC, res):
    p = KP.get(name)
    if not p:
        return 0
    pts = 0
    for rnd, key in ROUND_PRED.items():
        if rnd == 'F':
            if p.get('final') and p['final'] in res['F']:
                pts += SC['F']
        else:
            for t in p.get(key, []):
                if t in res[rnd]:
                    pts += SC[rnd]
    return pts


def ranks(names, tot):
    return {n: sum(1 for m in names if tot[m] > tot[n]) + 1 for n in names}


def picked_winner(KP, name, rnd, team):
    p = KP.get(name, {})
    if rnd == 'F':
        return team in p.get('final', '')
    key = ROUND_PRED.get(rnd)  # None for non-scored rounds (e.g. 3rd-place play-off)
    return bool(key) and team in p.get(key, [])


def main():
    KP, SC, dates, PP = load()
    NAMES = list(KP.keys())
    N = len(NAMES)
    played, fixtures = fetch_ko(dates)

    recap_date = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.now(timezone.utc).astimezone(PT).strftime('%Y-%m-%d')

    res_end = winners_through(played, recap_date)
    res_start = winners_through(played, (datetime.strptime(recap_date, '%Y-%m-%d').date()
                                         - timedelta(days=1)).strftime('%Y-%m-%d'))
    tot_end = {n: score(n, KP, SC, res_end) for n in NAMES}
    tot_start = {n: score(n, KP, SC, res_start) for n in NAMES}
    r_end, r_start = ranks(NAMES, tot_end), ranks(NAMES, tot_start)

    # Overall (group + knockout) standings for the cash prize pool.
    gtot = group_totals(recap_date)
    overall = sorted(((n, gtot.get(n, 0) + tot_end.get(n, 0), gtot.get(n, 0), tot_end.get(n, 0))
                      for n in PP), key=lambda x: (-x[1], short(x[0])))
    o_rank = {}
    for i, (n, tot, _g, _k) in enumerate(overall):
        o_rank[n] = 1 + sum(1 for _, t, _, _ in overall if t > tot)

    print(f'================= RECAP for {recap_date} =================\n')
    print('💰 PRIZE POOL — OVERALL (group + knockout)')
    for n, tot, g, ko in overall:
        print(f'  {o_rank[n]:>2}. {short(n):<14} {tot:>4}  (grp {g} + ko {ko})')

    print(f'\n(round winners decided so far: R32 {len(res_end["R32"])}/16, '
          f'R16 {len(res_end["R16"])}/8, QF {len(res_end["QF"])}/4, '
          f'SF {len(res_end["SF"])}/2, F {len(res_end["F"])}/1)\n')

    print('KNOCKOUT LEADERBOARD (end of day)  [move vs start of day]')
    for n in sorted(NAMES, key=lambda n: (r_end[n], -tot_end[n], short(n))):
        d = r_start[n] - r_end[n]
        mv = f'+{d}' if d > 0 else (str(d) if d < 0 else '–')
        gained = tot_end[n] - tot_start[n]
        g = f'  (+{gained} today)' if gained else ''
        print(f'  {r_end[n]:>2}. {short(n):<14} {tot_end[n]:>4}   move {mv}{g}')

    todays = played.get(recap_date, [])
    print(f"\nMATCHES on {recap_date}:")
    if not todays:
        print('  (none completed)')
    for rnd, win, lose, h, a, sc in todays:
        pct = sum(1 for n in NAMES if picked_winner(KP, n, rnd, win))
        who = [short(n) for n in NAMES if picked_winner(KP, n, rnd, win)]
        print(f'  [{rnd}] {h} {sc} {a}  -> {win} through ({pct}/{N} called it: {", ".join(who) or "nobody"})')

    # bracket-busters: today's eliminated teams that people had advancing further
    print('\nBRACKET-BUSTERS (eliminated teams people had going deeper):')
    any_bust = False
    for rnd, win, lose, *_ in todays:
        deep = []
        for n in NAMES:
            p = KP[n]
            rs = [R for R in ('R16', 'QF', 'SF') if lose in p.get(ROUND_PRED[R], [])]
            if p.get('final') == lose:
                rs.append('CHAMPION')
            if rs:
                deep.append(f'{short(n)}[{"/".join(rs)}]')
        if deep:
            any_bust = True
            print(f'  {lose} out -> {", ".join(deep)}')
    if not any_bust:
        print('  (none)')

    # next slate — the next date that has a bracket-scored round (skip the
    # third-place play-off, which isn't part of anyone's bracket picks)
    scored = lambda d: any(r in ROUND_PRED for r, *_ in fixtures[d])
    future = sorted(d for d in fixtures if d > recap_date and scored(d))
    nxt = future[0] if future else None
    print(f"\nNEXT SLATE: {nxt or '(none scheduled yet)'}")
    if nxt:
        for rnd, h, a, done in fixtures[nxt]:
            if rnd not in ROUND_PRED:
                continue
            sh = sum(1 for n in NAMES if picked_winner(KP, n, rnd, h))
            sa = sum(1 for n in NAMES if picked_winner(KP, n, rnd, a))
            label = '?' if (not h or not a) else f'{h} ({sh}/{N}) vs {a} ({sa}/{N})'
            print(f'  [{rnd}] {label}')

    # ---------------- Discord draft ----------------
    def md(dstr):
        _, m, d = dstr.split('-')
        return f'{int(m)}/{int(d)}'

    medals = {1: '🥇', 2: '🥈', 3: '🥉'}
    top = sorted([n for n in NAMES if r_end[n] <= 3], key=lambda n: (r_end[n], short(n)))
    by_rank = {}
    for n in top:
        by_rank.setdefault(r_end[n], []).append(n)
    top_lines = []
    for rk in sorted(by_rank):
        grp = by_rank[rk]
        names = ' / '.join(short(n) for n in grp)
        tie = ' _(tied)_' if len(grp) > 1 else ''
        top_lines.append(f'{medals.get(rk, "")} **{rk}. {names} — {tot_end[grp[0]]}**{tie}')

    # upsets: today's winners called by <= a third of the field
    shocks = []
    for rnd, win, lose, h, a, sc in sorted(todays, key=lambda x: -1):
        pct = sum(1 for n in NAMES if picked_winner(KP, n, rnd, win))
        if pct * 3 <= N:  # called by a third or fewer
            solo = [short(n) for n in NAMES if picked_winner(KP, n, rnd, win)]
            tag = f'only {solo[0]} called it' if len(solo) == 1 else f'{pct}/{N} called it'
            shocks.append(f'**{win}** bounced {lose} ({tag})')
    # champion-pick eliminations are always headline material
    busted = []
    for rnd, win, lose, *_ in todays:
        champs = [short(n) for n in NAMES if KP[n].get('final') == lose]
        if champs:
            busted.append(f'{lose} (champion pick of {", ".join(champs)}) is OUT')

    # overall prize-pool Top 3 (ties share a rank/line)
    o_top = [n for n, _, _, _ in overall if o_rank[n] <= 3]
    o_by_rank = {}
    for n in o_top:
        o_by_rank.setdefault(o_rank[n], []).append(n)
    o_tot = {n: t for n, t, _, _ in overall}
    o_lines = []
    for rk in sorted(o_by_rank):
        grp = o_by_rank[rk]
        names = ' & '.join(short(n) for n in grp) if len(grp) <= 2 else ', '.join(short(n) for n in grp)
        tie = ' _(tied)_' if len(grp) > 1 else ''
        o_lines.append(f'{medals.get(rk, "")} **{rk}. {names} — {o_tot[grp[0]]}**{tie}')
    # clear-leader flourish
    if o_lines and len(o_by_rank.get(1, [])) == 1:
        lead = o_by_rank[1][0]
        rest = [t for n, t, _, _ in overall if o_rank[n] > 1]
        if rest and o_tot[lead] - max(rest) > 0:
            o_lines[0] += f' 👑 (+{o_tot[lead] - max(rest)} clear)'

    LINK = 'https://buoybuoy.github.io/2026worldcup/'
    out = [f"## 🏆 World Cup Pick'em — {md(recap_date)} Recap", '']
    if o_lines:
        out.append('**💰 Prize Pool — Overall Top 3** _(group + knockout)_')
        out.extend(o_lines)
        out.append('')
    out.append('**🏟️ Knockout Top 3**')
    out.extend(top_lines)
    if shocks:
        out += ['', '⚡ **Biggest shocks:** ' + '; '.join(shocks) + '.']
    if busted:
        out += ['', '💀 **Bracket-buster:** ' + '; '.join(busted) + '.']
    if nxt:
        seg = []
        for rnd, h, a, done in fixtures[nxt]:
            if h and a and rnd in ROUND_PRED:
                sh = sum(1 for n in NAMES if picked_winner(KP, n, rnd, h))
                sa = sum(1 for n in NAMES if picked_winner(KP, n, rnd, a))
                fav, fc = (h, sh) if sh >= sa else (a, sa)
                seg.append(f'- **{h} vs {a}** — {fc}/{N} on {fav}')
        if seg:
            out += ['', f'🔮 **Next up ({md(nxt)}):**'] + seg
    out += ['', f'📲 Live standings & bracket: {LINK}']

    print('\n' + '=' * 56)
    print('DISCORD DRAFT (copy everything below):\n')
    print('\n'.join(out))
    print('\n' + '=' * 56)
    print('Tip: paste this back to your assistant to punch up the commentary.')


if __name__ == '__main__':
    main()
