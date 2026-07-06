#!/usr/bin/env python3
"""Fetch 2026 World Cup scores AND kickoff times from ESPN, update index.html.

- Scores: completed matches -> CONFIRMED_SCORES (full rewrite).
- Kickoff times/dates: every scheduled match -> MATCH_TIMES / SCHEDULE,
  updated in place per match key (preserves the hand-organized layout and
  any future games ESPN hasn't published yet).
All times are converted to Pacific Time, matching how the app displays them.
"""

import re
import requests
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo('America/Los_Angeles')

# ESPN display name → HTML name
NAME_MAP = {
    'Czech Republic': 'Czechia',
    "Côte d'Ivoire": 'Ivory Coast',
    'Ivory Coast': 'Ivory Coast',
    'Turkey': 'Türkiye',
    'Bosnia-Herzegovina': 'Bosnia and Herzegovina',
    'Cabo Verde': 'Cape Verde',
    'United States': 'USA',
    'Korea Republic': 'South Korea',
    'Congo DR': 'DR Congo',
    'Democratic Republic of Congo': 'DR Congo',
    'Curacao': 'Curaçao',
    'IR Iran': 'Iran',
}

GROUPS = {
    'A': ['Mexico', 'South Korea', 'South Africa', 'Czechia'],
    'B': ['Canada', 'Switzerland', 'Qatar', 'Bosnia and Herzegovina'],
    'C': ['Brazil', 'Morocco', 'Scotland', 'Haiti'],
    'D': ['USA', 'Paraguay', 'Australia', 'Türkiye'],
    'E': ['Germany', 'Ecuador', 'Ivory Coast', 'Curaçao'],
    'F': ['Netherlands', 'Japan', 'Tunisia', 'Sweden'],
    'G': ['Belgium', 'Egypt', 'Iran', 'New Zealand'],
    'H': ['Spain', 'Uruguay', 'Saudi Arabia', 'Cape Verde'],
    'I': ['France', 'Senegal', 'Norway', 'Iraq'],
    'J': ['Argentina', 'Algeria', 'Austria', 'Jordan'],
    'K': ['Portugal', 'Colombia', 'Uzbekistan', 'DR Congo'],
    'L': ['England', 'Croatia', 'Ghana', 'Panama'],
}

MATCHES = {
    'A': [['Mexico','South Africa'],['South Korea','Czechia'],['Mexico','South Korea'],['South Africa','Czechia'],['Mexico','Czechia'],['South Africa','South Korea']],
    'B': [['Canada','Bosnia and Herzegovina'],['Qatar','Switzerland'],['Canada','Qatar'],['Switzerland','Bosnia and Herzegovina'],['Canada','Switzerland'],['Bosnia and Herzegovina','Qatar']],
    'C': [['Brazil','Morocco'],['Scotland','Haiti'],['Brazil','Scotland'],['Morocco','Haiti'],['Brazil','Haiti'],['Scotland','Morocco']],
    'D': [['USA','Paraguay'],['Australia','Türkiye'],['USA','Australia'],['Paraguay','Türkiye'],['USA','Türkiye'],['Australia','Paraguay']],
    'E': [['Germany','Curaçao'],['Ecuador','Ivory Coast'],['Germany','Ecuador'],['Ivory Coast','Curaçao'],['Germany','Ivory Coast'],['Ecuador','Curaçao']],
    'F': [['Netherlands','Japan'],['Tunisia','Sweden'],['Netherlands','Tunisia'],['Japan','Sweden'],['Netherlands','Sweden'],['Japan','Tunisia']],
    'G': [['Belgium','Egypt'],['Iran','New Zealand'],['Belgium','Iran'],['Egypt','New Zealand'],['Belgium','New Zealand'],['Egypt','Iran']],
    'H': [['Spain','Cape Verde'],['Uruguay','Saudi Arabia'],['Spain','Uruguay'],['Saudi Arabia','Cape Verde'],['Spain','Saudi Arabia'],['Uruguay','Cape Verde']],
    'I': [['France','Senegal'],['Iraq','Norway'],['France','Iraq'],['Senegal','Norway'],['France','Norway'],['Senegal','Iraq']],
    'J': [['Argentina','Algeria'],['Austria','Jordan'],['Argentina','Austria'],['Algeria','Jordan'],['Argentina','Jordan'],['Algeria','Austria']],
    'K': [['Portugal','Uzbekistan'],['Colombia','DR Congo'],['Portugal','Colombia'],['Uzbekistan','DR Congo'],['Portugal','DR Congo'],['Uzbekistan','Colombia']],
    'L': [['England','Ghana'],['Panama','Croatia'],['England','Panama'],['Croatia','Ghana'],['England','Croatia'],['Panama','Ghana']],
}

TEAM_TO_GROUP = {team: g for g, teams in GROUPS.items() for team in teams}

# Knockout: results are stored as the set of winners per round (membership
# scoring on the front-end, so order doesn't matter).
KO_ROUND_ORDER = ['r32', 'r16', 'qf', 'sf', 'final']
KO_ROUND_SIZE = {'r32': 16, 'r16': 8, 'qf': 4, 'sf': 2}


def _ko_wins(ko, team):
    return sum(team in ko[r] for r in ('r32', 'r16', 'qf', 'sf')) + (1 if ko['final'] == team else 0)


def record_ko(ko, home, away, winner):
    """Record a completed knockout winner into the round inferred from how many
    rounds each team has already won, and the loser into the eliminated set.
    Skips the 3rd-place playoff winner (whose inferred round is already full)."""
    loser = away if winner == home else home
    if loser not in ko['out']:
        ko['out'].append(loser)
    rd = KO_ROUND_ORDER[min(_ko_wins(ko, home), _ko_wins(ko, away))]
    if rd == 'final':
        ko['final'] = winner
    elif len(ko[rd]) < KO_ROUND_SIZE[rd] and winner not in ko[rd]:
        ko[rd].append(winner)


def normalize(name):
    return NAME_MAP.get(name, name)


def find_match_key(home, away):
    """Return (key, home_is_swapped) or (None, None) if not found."""
    g = TEAM_TO_GROUP.get(home)
    if not g or away not in GROUPS[g]:
        return None, None
    for h, a in MATCHES[g]:
        if h == home and a == away:
            return f'{g}|{h}|{a}', False
        if h == away and a == home:
            return f'{g}|{h}|{a}', True
    return None, None


def fetch_all():
    """Return (scores, times, dates, scorers, assists, channels, ko_results, ko_scores)."""
    scores, times, dates, scorers, assists, channels = {}, {}, {}, {}, {}, {}
    ko_results = {'r32': [], 'r16': [], 'qf': [], 'sf': [], 'final': '', 'out': []}
    ko_scores = {}
    start = date(2026, 6, 11)
    end = date(2026, 7, 19)  # through the final, so the golden boot keeps tallying
    current = start

    while current <= end:
        date_str = current.strftime('%Y%m%d')
        url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={date_str}'
        try:
            r = requests.get(url, timeout=15, headers={'User-Agent': 'WorldCupTracker/1.0'})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f'  Skipping {date_str}: {e}')
            current += timedelta(days=1)
            continue

        for event in data.get('events', []):
            try:
                comp = event['competitions'][0]
                home_data = next(c for c in comp['competitors'] if c.get('homeAway') == 'home')
                away_data = next(c for c in comp['competitors'] if c.get('homeAway') == 'away')

                home = normalize(home_data['team']['displayName'])
                away = normalize(away_data['team']['displayName'])

                # Goal scorers — tally for the Golden Boot (completed matches,
                # own goals excluded). Independent of group-match mapping so it
                # also works for knockout games.
                if comp['status']['type'].get('completed'):
                    id2name = {str(c.get('id')): normalize(c['team']['displayName'])
                               for c in comp['competitors']}
                    for det in comp.get('details', []):
                        if not det.get('scoringPlay') or det.get('shootout'):
                            continue  # skip penalty-shootout kicks (not real goals)
                        if det.get('ownGoal') or 'own goal' in ((det.get('type') or {}).get('text') or '').lower():
                            continue
                        ath = det.get('athletesInvolved') or []
                        pname = ath[0].get('displayName') if ath else None
                        tname = id2name.get(str((det.get('team') or {}).get('id')))
                        if pname and tname:
                            scorers[(pname, tname)] = scorers.get((pname, tname), 0) + 1

                    # Assists — the scoreboard feed omits them, so read the match
                    # summary's per-player goalAssists (Golden Boot tiebreaker).
                    try:
                        sr = requests.get(
                            f'https://site.api.espn.com/apis/site/v2/sports/soccer/'
                            f'fifa.world/summary?event={event["id"]}',
                            timeout=15, headers={'User-Agent': 'WorldCupTracker/1.0'})
                        for roster in sr.json().get('rosters', []):
                            rt = normalize(roster.get('team', {}).get('displayName', ''))
                            for e in roster.get('roster', []):
                                pn = (e.get('athlete') or {}).get('displayName')
                                st = {x.get('name'): x.get('value') for x in e.get('stats', [])}
                                a = int(st.get('goalAssists') or 0)
                                if pn and rt and a:
                                    assists[(pn, rt)] = assists.get((pn, rt), 0) + a
                    except Exception as ex:
                        print(f'  Assist fetch failed for {event.get("id")}: {ex}')

                key, swapped = find_match_key(home, away)
                if key is None:
                    # Cross-group match = knockout. Record the winner by round.
                    # (Dates are processed in order, so earlier rounds land first.)
                    if comp['status']['type'].get('completed'):
                        hg = int(float(home_data['score']))
                        ag = int(float(away_data['score']))
                        pair = sorted([home, away])
                        goals = {home: hg, away: ag}
                        ko_scores['|'.join(pair)] = '%d-%d' % (goals[pair[0]], goals[pair[1]])
                        winner = (home if home_data.get('winner')
                                  else away if away_data.get('winner') else None)
                        if winner:
                            record_ko(ko_results, home, away, winner)
                            print(f'  KO {winner} advances ({home} {hg}-{ag} {away})')
                        else:
                            print(f'  KO no winner flag: {home} vs {away}')
                    continue

                # Kickoff time/date in Pacific Time (every scheduled match)
                iso = event.get('date')  # e.g. 2026-06-16T19:00Z
                if iso:
                    utc_dt = datetime.strptime(iso, '%Y-%m-%dT%H:%MZ').replace(tzinfo=timezone.utc)
                    pt_dt = utc_dt.astimezone(PT)
                    times[key] = pt_dt.strftime('%H:%M')
                    dates[key] = pt_dt.strftime('%Y-%m-%d')

                # US English broadcast channel (FOX / FS1)
                bc = comp.get('broadcasts') or []
                names = bc[0].get('names') if bc and bc[0].get('names') else []
                chan = next((n for n in names if n in ('FOX', 'FS1')), names[0] if names else '')
                if chan:
                    channels[key] = chan

                # Final score (completed only)
                if comp['status']['type'].get('completed'):
                    hs = str(int(float(home_data['score'])))
                    as_ = str(int(float(away_data['score'])))
                    scores[key] = [as_, hs] if swapped else [hs, as_]
                    print(f'  {key}: {hs}-{as_}')
            except Exception as e:
                print(f'  Event error: {e}')

        current += timedelta(days=1)

    return scores, times, dates, scorers, assists, channels, ko_results, ko_scores


def update_html(filepath, scores, times, dates, scorers, assists, channels, ko_results, ko_scores):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()
    original = content

    # CHANNELS — full rewrite (broadcast channel per match)
    if channels:
        channels_js = '{' + ','.join(
            f"'{k}':'{v}'" for k, v in sorted(channels.items())
        ) + '}'
        content = re.sub(r'const CHANNELS = \{[^}]*\};',
                         f'const CHANNELS = {channels_js};', content)

    # 1) CONFIRMED_SCORES — full rewrite
    scores_js = '{' + ','.join(
        f"'{k}':['{v[0]}','{v[1]}']" for k, v in sorted(scores.items())
    ) + '}'
    content = re.sub(
        r'const CONFIRMED_SCORES = \{[^}]*\};',
        f'const CONFIRMED_SCORES = {scores_js};',
        content,
    )

    # 2) MATCH_TIMES — update each key's HH:MM in place
    time_changes = 0
    for k, v in times.items():
        pat = r"('" + re.escape(k) + r"':')(\d{1,2}:\d{2})(')"
        new_content, n = re.subn(pat, lambda m, v=v: m.group(1) + v + m.group(3), content)
        if n and new_content != content:
            time_changes += 1
        content = new_content

    # 3) SCHEDULE — update each key's date in place
    date_changes = 0
    for k, v in dates.items():
        pat = r"('" + re.escape(k) + r"':')(\d{4}-\d{2}-\d{2})(')"
        new_content, n = re.subn(pat, lambda m, v=v: m.group(1) + v + m.group(3), content)
        if n and new_content != content:
            date_changes += 1
        content = new_content

    # 4) SCORERS — Golden Boot tally, sorted by goals desc, then the tiebreakers:
    #    most assists, then name (fewest-minutes tiebreaker not tracked yet).
    def esc(s):
        return s.replace('\\', '\\\\').replace("'", "\\'")
    rows = sorted(scorers.items(),
                  key=lambda kv: (-kv[1], -assists.get(kv[0], 0), kv[0][0]))
    scorers_js = '[' + ','.join(
        "{p:'%s',t:'%s',g:%d,a:%d}" % (esc(p), esc(t), n, assists.get((p, t), 0))
        for (p, t), n in rows
    ) + ']'
    content = re.sub(r'const SCORERS=\[[^\]]*\];', f'const SCORERS={scorers_js};', content)

    # 5) KO_RESULTS — knockout winners per round, full rewrite
    def arr(lst):
        return '[' + ','.join("'%s'" % esc(t) for t in lst) + ']'
    ko_js = '{r32:%s,r16:%s,qf:%s,sf:%s,final:%s,out:%s}' % (
        arr(ko_results['r32']), arr(ko_results['r16']), arr(ko_results['qf']),
        arr(ko_results['sf']), ("'%s'" % esc(ko_results['final'])) if ko_results['final'] else "''",
        arr(ko_results['out']),
    )
    content = re.sub(r'const KO_RESULTS=\{[^}]*\};', f'const KO_RESULTS={ko_js};', content)

    # 6) KO_SCORES — knockout final scores, flat {sortedPair:'g0-g1'}
    ko_scores_js = '{' + ','.join(
        "'%s':'%s'" % (esc(k), v) for k, v in sorted(ko_scores.items())
    ) + '}'
    content = re.sub(r'const KO_SCORES=\{[^}]*\};', f'const KO_SCORES={ko_scores_js};', content)

    if content == original:
        print('No changes to index.html.')
        return False

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Updated index.html — {len(scores)} scores, {time_changes} kickoff times, '
          f'{date_changes} dates, {len(rows)} scorers, {len(channels)} channels.')
    return True


if __name__ == '__main__':
    print('Fetching scores, kickoff times, scorers, and channels...')
    scores, times, dates, scorers, assists, channels, ko_results, ko_scores = fetch_all()
    ko_n = sum(len(ko_results[r]) for r in ('r32', 'r16', 'qf', 'sf')) + (1 if ko_results['final'] else 0)
    print(f'Found {len(scores)} completed matches, {len(times)} scheduled kickoffs, '
          f'{len(scorers)} scorers, {len(channels)} channels, {ko_n} knockout results, '
          f'{len(ko_scores)} knockout scores.')
    update_html('index.html', scores, times, dates, scorers, assists, channels, ko_results, ko_scores)
