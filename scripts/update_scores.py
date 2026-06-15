#!/usr/bin/env python3
"""Fetch completed 2026 World Cup scores from ESPN and update index.html."""

import re
import requests
from datetime import date, timedelta

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


def fetch_scores():
    scores = {}
    start = date(2026, 6, 11)
    end = date(2026, 6, 27)
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
                if not comp['status']['type'].get('completed'):
                    continue

                home_data = next(c for c in comp['competitors'] if c.get('homeAway') == 'home')
                away_data = next(c for c in comp['competitors'] if c.get('homeAway') == 'away')

                home = normalize(home_data['team']['displayName'])
                away = normalize(away_data['team']['displayName'])
                hs = str(int(float(home_data['score'])))
                as_ = str(int(float(away_data['score'])))

                key, swapped = find_match_key(home, away)
                if key is None:
                    print(f'  No key for: {home} vs {away}')
                    continue

                scores[key] = [as_, hs] if swapped else [hs, as_]
                print(f'  {key}: {hs}-{as_}')
            except Exception as e:
                print(f'  Event error: {e}')

        current += timedelta(days=1)

    return scores


def update_html(filepath, scores):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    scores_js = '{' + ','.join(
        f"'{k}':['{v[0]}','{v[1]}']"
        for k, v in sorted(scores.items())
    ) + '}'

    updated = re.sub(
        r'const CONFIRMED_SCORES = \{[^}]*\};',
        f'const CONFIRMED_SCORES = {scores_js};',
        content,
    )

    if updated == content:
        print('No changes to index.html.')
        return False

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(updated)

    print(f'Updated index.html with {len(scores)} scores.')
    return True


if __name__ == '__main__':
    print('Fetching scores...')
    scores = fetch_scores()
    print(f'Found {len(scores)} completed matches.')
    update_html('index.html', scores)
