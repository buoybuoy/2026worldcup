#!/usr/bin/env python3
"""How many knockout brackets are still intact.

A companion to ko_recap.py. For each participant's KNOCKOUT_PREDS it measures
how deep their predicted bracket is still unbroken — i.e. how many of their
picks at each round are on teams that are still alive in the tournament.

A pick is "alive" if its team won the Round of 32 and hasn't been eliminated
since, so this stays correct as the tournament rolls on (R16, QF, SF, Final):
    alive = set(KO_RESULTS.r32) - set(KO_RESULTS.out)

Tiers, deepest first:
    Perfect R32  — all 16 R32-winner picks advanced
    Elite Eight  — all 8 quarterfinal picks still alive
    Final Four   — all 4 semifinal picks still alive
    Finalists    — both final picks still alive
    Champion     — the title pick is still alive

Reads the committed bracket state from index.html (the same data the live site
scores from); run a recap first if you want the very latest results pulled in.

Usage:
    python3 scripts/bracket_survival.py
"""

import re
import json

HERE = __file__.rsplit('/', 2)[0]
INDEX = HERE + '/index.html'
SHORT = {'光榮的人民主席 Chairman Lau': 'Chairman Lau', '대한민국 남바원': '남바원'}
def short(n): return SHORT.get(n, n)


def load():
    html = open(INDEX, encoding='utf-8').read()
    kp = re.search(r'const KNOCKOUT_PREDS\s*=\s*\{(.*?)\n\};', html, re.S).group(1)
    s = ('{' + kp.rstrip().rstrip(',') + '}').replace("'", '"')
    s = re.sub(r'([{,])\s*(r32|r16|qf|sf|final)\s*:', r'\1"\2":', s)
    KP = json.loads(s)
    kr = re.search(r'const KO_RESULTS=\{(.*?)\};', html, re.S).group(1)

    def arr(key):
        m = re.search(key + r':\[(.*?)\]', kr)
        return set(re.findall(r"'([^']+)'", m.group(1))) if m else set()

    return KP, arr('r32'), arr('r16'), arr('qf'), arr('sf'), arr('out')


def main():
    KP, r32w, r16w, qfw, sfw, out = load()
    alive = r32w - out            # teams still standing
    decided = len(r32w) + len(out)  # rough sense of how far along we are

    rows = []
    for n in KP:
        p = KP[n]
        r32c = sum(1 for t in p.get('r32', []) if t in alive)   # of 16
        e8 = sum(1 for t in p.get('r16', []) if t in alive)     # of 8
        f4 = sum(1 for t in p.get('qf', []) if t in alive)      # of 4
        fin = sum(1 for t in p.get('sf', []) if t in alive)     # of 2
        champ = p.get('final', '') in alive
        # deepest fully-intact tier (higher = more intact)
        depth = (5 if r32c == 16 else 4 if e8 == 8 else 3 if f4 == 4
                 else 2 if fin == 2 else 1 if champ else 0)
        rows.append({'n': short(n), 'r32': r32c, 'e8': e8, 'f4': f4,
                     'fin': fin, 'champ': champ, 'depth': depth})

    N = len(rows)
    rows.sort(key=lambda r: (-r['depth'], -r['r32'], -r['e8'], -r['f4'], r['n']))

    print(f'============ BRACKET SURVIVAL ({len(alive)} teams still alive) ============\n')
    print(f'{"name":<14} R32/16  QF8  SF4  F2  Champion')
    for r in rows:
        print(f'{r["n"]:<14} {r["r32"]:>5}   {r["e8"]:>3} {r["f4"]:>4} {r["fin"]:>3}   '
              f'{"ALIVE" if r["champ"] else "OUT"}')

    perfect = sum(1 for r in rows if r['r32'] == 16)
    e8i = sum(1 for r in rows if r['e8'] == 8)
    f4i = sum(1 for r in rows if r['f4'] == 4)
    fini = sum(1 for r in rows if r['fin'] == 2)
    champi = sum(1 for r in rows if r['champ'])
    dead_champ = [r['n'] for r in rows if not r['champ']]

    TIERS = [('Perfect R32 (all 16 advancers)', perfect),
             ('Elite Eight intact (all 8 QF picks alive)', e8i),
             ('Final Four intact (all 4 SF picks alive)', f4i),
             ('Both finalists alive', fini),
             ('Champion still alive', champi)]
    print('\nHOW MANY STILL INTACT:')
    for label, cnt in TIERS:
        print(f'  {label}: {cnt}/{N}')
    best_r32 = max(r['r32'] for r in rows)
    print(f'\n  Best R32 record: {best_r32}/16 '
          f'({", ".join(r["n"] for r in rows if r["r32"] == best_r32)})')
    if dead_champ:
        print(f'  Champion eliminated: {", ".join(dead_champ)}')

    # ---- Discord draft ----
    top_tier = next((lbl for lbl, c in TIERS if c > 0), None)
    leaders = [r['n'] for r in rows if r['depth'] == rows[0]['depth']]
    out_lines = ['## 🧩 Bracket Survival Check', '']
    out_lines.append('**How many brackets are still intact, by depth:**')
    icons = ['💯', '💪', '🎯', '🥈', '🏆']
    for (label, cnt), ic in zip(TIERS, icons):
        out_lines.append(f'{ic} **{label.split(" (")[0]}:** {cnt}/{N}')
    if rows[0]['depth'] >= 1:
        out_lines += ['', f'🥇 **Most intact:** {", ".join(leaders)} '
                          f'— deepest bracket{"s" if len(leaders) > 1 else ""} still standing.']
    if dead_champ:
        out_lines += ['', f'💀 **Champion already out:** {", ".join(dead_champ)}.']
    out_lines += ['', '📲 https://buoybuoy.github.io/2026worldcup/']
    print('\n' + '=' * 56)
    print('DISCORD DRAFT (copy below):\n')
    print('\n'.join(out_lines))


if __name__ == '__main__':
    main()
