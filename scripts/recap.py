#!/usr/bin/env python3
"""Daily predictions-leaderboard recap generator.

Pulls the latest results from ESPN (merged with committed scores), recomputes
the leaderboard with the app's exact scoring, works out the day's movement,
the chalk/chaos groups, and the next day's fixtures + everyone's picks — then
prints a stats block and a draft recap you can polish.

Usage:
    python3 scripts/recap.py            # recap for today (PT)
    python3 scripts/recap.py 2026-06-18 # recap for a specific day
"""

import re
import sys
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import Counter

PT = ZoneInfo('America/Los_Angeles')
POS_PTS = [25, 15, 10, 5]
HERE = __file__.rsplit('/', 2)[0]
INDEX = HERE + '/index.html'

# ESPN display name -> app name
NAME_MAP = {
    'Czech Republic': 'Czechia', "Côte d'Ivoire": 'Ivory Coast', 'Turkey': 'Türkiye',
    'Bosnia-Herzegovina': 'Bosnia and Herzegovina', 'Cabo Verde': 'Cape Verde',
    'United States': 'USA', 'Korea Republic': 'South Korea', 'Congo DR': 'DR Congo',
    'Democratic Republic of Congo': 'DR Congo', 'Curacao': 'Curaçao', 'IR Iran': 'Iran',
}
SHORT = {'光榮的人民主席 Chairman Lau': 'Chairman Lau', '대한민국 남바원': '남바원'}
def short(n): return SHORT.get(n, n)


# ---- parse constants from index.html -------------------------------------
def load():
    html = open(INDEX, encoding='utf-8').read()

    def grab(name):
        return re.search(r'const ' + name + r'\s*=\s*(\{.*?\});', html, re.S).group(1)

    def js2py(s):
        s = re.sub(r'//[^\n]*', '', s)
        s = s.replace("'", '"')
        s = re.sub(r'([{,])\s*([A-L])\s*:', r'\1"\2":', s)
        s = re.sub(r',(\s*[}\]])', r'\1', s)
        return json.loads(s)

    G = js2py(grab('GROUPS'))
    M = js2py(grab('MATCHES'))
    SCH = js2py(grab('SCHEDULE'))
    MT = js2py(grab('MATCH_TIMES'))
    CONF = js2py(grab('CONFIRMED_SCORES'))
    fp = re.search(r'const FRIENDS_PREDS\s*=\s*\{(.*?)\n\};', html, re.S).group(1)
    FP = js2py('{' + fp + '\n}')
    tp = re.search(r'const THIRD_PICKS\s*=\s*\{(.*?)\n\};', html, re.S).group(1)
    TP = json.loads('{' + tp.replace("'", '"').rstrip().rstrip(',') + '}')
    return G, M, SCH, MT, CONF, FP, TP


# ---- live results from ESPN ----------------------------------------------
def fetch_live(GROUPS, MATCHES, base):
    T2G = {t: g for g in GROUPS for t in GROUPS[g]}

    def find_key(home, away):
        g = T2G.get(home)
        if not g or away not in GROUPS[g]:
            return None, None
        for h, a in MATCHES[g]:
            if h == home and a == away:
                return f'{g}|{h}|{a}', False
            if h == away and a == home:
                return f'{g}|{h}|{a}', True
        return None, None

    scores = dict(base)
    today = datetime.now(timezone.utc).astimezone(PT)
    dates = {(today + timedelta(days=off)).strftime('%Y%m%d') for off in range(-7, 2)}
    for d in sorted(dates):
        url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={d}'
        try:
            data = json.load(urllib.request.urlopen(url, timeout=15))
        except Exception:
            continue
        for ev in data.get('events', []):
            c = ev['competitions'][0]
            if not c['status']['type'].get('completed'):
                continue
            try:
                hc = next(x for x in c['competitors'] if x['homeAway'] == 'home')
                ac = next(x for x in c['competitors'] if x['homeAway'] == 'away')
                hn = NAME_MAP.get(hc['team']['displayName'], hc['team']['displayName'])
                an = NAME_MAP.get(ac['team']['displayName'], ac['team']['displayName'])
                key, sw = find_key(hn, an)
                if not key:
                    continue
                hs, as_ = str(int(float(hc['score']))), str(int(float(ac['score'])))
                scores[key] = [as_, hs] if sw else [hs, as_]
            except Exception:
                continue
    return scores


# ---- scoring (mirrors the app) -------------------------------------------
def make_scorers(GROUPS, MATCHES, FP, TP):
    def standings(g, sc):
        T = {t: {'gf': 0, 'ga': 0, 'pts': 0} for t in GROUPS[g]}
        for mh, ma in MATCHES[g]:
            x = sc.get(g + '|' + mh + '|' + ma)
            if not x or x[0] == '' or x[1] == '':
                continue
            h, a = int(x[0]), int(x[1])
            T[mh]['gf'] += h; T[mh]['ga'] += a; T[ma]['gf'] += a; T[ma]['ga'] += h
            if h > a: T[mh]['pts'] += 3
            elif h < a: T[ma]['pts'] += 3
            else: T[mh]['pts'] += 1; T[ma]['pts'] += 1
        order = sorted(GROUPS[g], key=lambda t: (-T[t]['pts'], -(T[t]['gf'] - T[t]['ga']), -T[t]['gf']))
        return order, T

    def played(g, sc):
        return sum(1 for mh, ma in MATCHES[g]
                   if sc.get(g + '|' + mh + '|' + ma) and sc[g + '|' + mh + '|' + ma][0] != '')

    def advancing(sc):
        th = []
        for g in GROUPS:
            if played(g, sc) == 0:
                continue
            o, T = standings(g, sc)
            t = o[2]
            th.append((t, T[t]['pts'], T[t]['gf'] - T[t]['ga'], T[t]['gf']))
        th.sort(key=lambda x: (-x[1], -x[2], -x[3]))
        return [t[0] for t in th[:8]]

    def group_pts(name, g, sc):
        o, _ = standings(g, sc)
        p = FP[name][g]
        s = sum(POS_PTS[i] for i in range(4) if o[i] == p[i])
        if played(g, sc) == 6 and all(o[i] == p[i] for i in range(4)):
            s += 10
        return s

    def total(name, sc, adv):
        gp = sum(group_pts(name, g, sc) for g in GROUPS if played(g, sc) > 0)
        tp = 5 * sum(1 for x in TP.get(name, []) if x in adv)
        return gp + tp, gp, tp

    return standings, played, advancing, group_pts, total


def ranks(NAMES, totals):
    return {n: sum(1 for m in NAMES if totals[m] > totals[n]) + 1 for n in NAMES}


def main():
    G, M, SCH, MT, CONF, FP, TP = load()
    NAMES = list(FP.keys())
    scores = fetch_live(G, M, CONF)
    standings, played, advancing, group_pts, total = make_scorers(G, M, FP, TP)

    recap_date = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.now(timezone.utc).astimezone(PT).strftime('%Y-%m-%d')

    # score sets: end of recap day vs start of recap day
    end = {k: v for k, v in scores.items() if SCH.get(k, '9999') <= recap_date}
    start = {k: v for k, v in scores.items() if SCH.get(k, '9999') < recap_date}
    adv_end = advancing(end)
    tot_end = {n: total(n, end, adv_end)[0] for n in NAMES}
    det_end = {n: total(n, end, adv_end) for n in NAMES}
    adv_start = advancing(start)
    tot_start = {n: total(n, start, adv_start)[0] for n in NAMES}
    r_end = ranks(NAMES, tot_end)
    r_start = ranks(NAMES, tot_start)

    print(f'================= RECAP for {recap_date} =================')
    print(f'(games counted: {len(end)} completed through {recap_date})\n')

    print('LEADERBOARD (end of day)  [move vs start of day]')
    for n in sorted(NAMES, key=lambda n: (r_end[n], -tot_end[n])):
        t, gp, tp = det_end[n]
        d = r_start[n] - r_end[n]
        mv = f'+{d}' if d > 0 else (str(d) if d < 0 else '–')
        print(f'  {r_end[n]:>2}. {short(n):<14} {t:>4}  (grp {gp}, 3rd {tp})   move {mv}')

    # groups that played on recap_date
    todays_groups = sorted({k.split('|')[0] for k, d in SCH.items() if d == recap_date})
    print(f"\nGROUPS IN ACTION on {recap_date}: {', '.join(todays_groups) or '(none)'}")
    print('\nPER-GROUP (current): standings | field avg pts | leader called by')
    for g in G:
        if played(g, end) == 0:
            continue
        o, _ = standings(g, end)
        avg = sum(group_pts(n, g, end) for n in NAMES) / len(NAMES)
        called = sum(1 for n in NAMES if FP[n][g][0] == o[0])
        flag = '  <-- CHAOS' if avg < 10 else ('  <-- chalk' if avg >= 28 else '')
        print(f'  {g}: {", ".join(o):<46} avg {avg:4.1f} | {o[0]} by {called}/{len(NAMES)}{flag}')

    # next day's fixtures + pick tallies
    future = sorted(d for d in set(SCH.values()) if d > recap_date)
    nxt = future[0] if future else None
    print(f"\nNEXT FIXTURES: {nxt or '(none)'}")
    if nxt:
        fx = [(k.split('|')[0], k.split('|')[1], k.split('|')[2]) for k, d in SCH.items() if d == nxt]
        for g, h, a in sorted(fx):
            tm = MT.get(f'{g}|{h}|{a}', '?')
            print(f'  Group {g}: {h} vs {a}  ({tm} PT)')
        nxt_groups = sorted({g for g, _, _ in fx})
        print('\n  Field picks for those groups (predicted WINNER):')
        for g in nxt_groups:
            tally = Counter(FP[n][g][0] for n in NAMES)
            print(f'   Group {g}: {dict(tally.most_common())}')
            for team, _c in tally.most_common():
                who = [short(n) for n in NAMES if FP[n][g][0] == team]
                print(f'       {team}: {", ".join(who)}')

    # ---- auto Discord draft ----------------------------------------------
    def md(dstr):
        _, m, d = dstr.split('-')
        return f'{int(m)}/{int(d)}'

    medals = {1: '🥇', 2: '🥈', 3: '🥉'}
    # group players who share a rank onto one line ("A & B — pts (tied)")
    top = sorted([n for n in NAMES if r_end[n] <= 3], key=lambda n: (r_end[n], short(n)))
    by_rank = {}
    for n in top:
        by_rank.setdefault(r_end[n], []).append(n)
    top_lines = []
    for rk in sorted(by_rank):
        grp = by_rank[rk]
        names = ' & '.join(short(n) for n in grp) if len(grp) <= 2 else ', '.join(short(n) for n in grp)
        tie = ' _(tied)_' if len(grp) > 1 else ''
        top_lines.append(f'{medals.get(rk, "")} **{rk}. {names} — {tot_end[grp[0]]}**{tie}')
    leaders = [n for n in NAMES if r_end[n] == 1]
    others = [tot_end[n] for n in NAMES if r_end[n] > 1]
    if len(leaders) == 1 and others:
        gap = tot_end[leaders[0]] - max(others)
        if gap > 0:
            top_lines[0] += f' 👑 (+{gap} clear!)'

    moves = {n: r_start[n] - r_end[n] for n in NAMES}
    climb = max(moves.items(), key=lambda kv: kv[1])
    fall = min(moves.items(), key=lambda kv: kv[1])
    mover = []
    if climb[1] > 0:
        mover.append(f'📈 **{short(climb[0])}** climbed {climb[1]} spot{"s" if climb[1] > 1 else ""} today.')
    if fall[1] < 0:
        mover.append(f'📉 **{short(fall[0])}** slid {-fall[1]}.')

    todays = sorted({k.split('|')[0] for k, d in SCH.items() if d == recap_date})
    drama = []
    for g in todays:
        if played(g, end) == 0:
            continue
        o, _ = standings(g, end)
        called = sum(1 for n in NAMES if FP[n][g][0] == o[0])
        if called <= 3:
            drama.append(f'**{o[0]}** atop Group {g} (only {called}/15 called it)')

    nxt_groups = sorted({k.split('|')[0] for k, d in SCH.items() if d == nxt}) if nxt else []
    tmrw_lines = []
    for g in nxt_groups:
        tally = Counter(FP[n][g][0] for n in NAMES)
        team, c = tally.most_common(1)[0]
        seg = f'**Group {g}:** {c}/15 on {team}'
        for t, cc in tally.items():
            if cc == 1:
                who = next(short(n) for n in NAMES if FP[n][g][0] == t)
                seg += f' · 🐺 {who} alone on {t}'
        tmrw_lines.append('- ' + seg)

    LINK = 'https://buoybuoy.github.io/2026worldcup/'
    out = []
    out.append(f'## 🏆 World Cup Pick\'em — {md(recap_date)} Recap')
    out.append('')
    out.append('**🥇 Top 3**')
    out.extend(top_lines)
    if mover:
        out.append('')
        out.append(' '.join(mover))
    if drama:
        out.append('')
        out.append('**Biggest shocks:** ' + '; '.join(drama) + '.')
    if tmrw_lines:
        out.append('')
        out.append(f'**🔮 Next up ({md(nxt)}):**')
        out.extend(tmrw_lines)
    out.append('')
    out.append(f'📲 Live standings & updates: {LINK}')

    print('\n' + '=' * 56)
    print('DISCORD DRAFT (copy everything below):\n')
    print('\n'.join(out))
    print('\n' + '=' * 56)
    print('Tip: paste this back to your assistant to punch up the commentary.')


if __name__ == '__main__':
    main()
