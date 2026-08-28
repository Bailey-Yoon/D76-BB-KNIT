#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8담당 DAILY MARKET BRIEF 의 원자재 데이터를 스냅샷으로 떠 온다.

허브는 GitHub Pages 라 브라우저에서 직접 받아 오면 좋겠지만, 원본 API 에는
Access-Control-Allow-Origin 이 없어 다른 출처에서 fetch 하면 막힌다.
그래서 GitHub Actions 가 서버에서 받아(=CORS 무관) data/raw-material.json 으로
커밋하고, 허브는 그 파일을 같은 출처에서 읽는다.

원본에 CORS 헤더가 붙으면 이 스냅샷은 걷어내고 허브가 API 를 직접 읽으면 된다.

두 곳을 합친다:
  /api/market                — Yahoo Finance 일일 스냅샷 (ICE 면화 선물, WTI, Brent)
  /api/raw-material-update   — 주간 원자재 리포트 (중국·인도 면화, PSF, DTY + 코멘트)

미국 면화는 선물 시세를 우선 쓰고, 없으면 주간 리포트 값으로 떨어진다.
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request

BASE = 'https://newsletter-for-div-8.vercel.app'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'data', 'raw-material.json')
TIMEOUT = 20


def get(path):
    req = urllib.request.Request(BASE + path, headers={
        'User-Agent': 'D76-BB-KNIT raw-material snapshot',
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def num(v):
    """숫자로 못 바꾸면 None — 0 으로 떨어뜨리면 '변동 없음'으로 잘못 보인다."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # NaN 제외


def tile(key, label, price, change, unit, note, comment=''):
    return {
        'key': key, 'label': label, 'price': num(price), 'changePct': num(change),
        'unit': unit, 'note': note, 'comment': (comment or '').strip(),
    }


def build(market, weekly):
    md = (market or {}).get('data') or {}
    rm = (weekly or {}).get('rawMaterials') or {}

    def q(sym):
        return md.get(sym) or {}

    def w(name):
        return rm.get(name) or {}

    us_fut = q('CTZ26.NYB')
    us_wk = w('usCotton')
    if num(us_fut.get('price')) is not None:
        us = tile('usCotton', 'U.S. Cotton', us_fut.get('price'), us_fut.get('changePct'),
                  '¢/lb', 'ICE Dec-26 선물', us_wk.get('comment'))
    else:
        us = tile('usCotton', 'U.S. Cotton', us_wk.get('price'), us_wk.get('changePct'),
                  us_wk.get('unit') or '¢/lb', '주간 리포트', us_wk.get('comment'))

    tiles = [us]
    for key, label in (('chinaCotton', 'China Cotton'),
                       ('indiaCotton', 'India Cotton'),
                       ('psf', 'PSF'), ('dty', 'DTY')):
        d = w(key)
        tiles.append(tile(key, label, d.get('price'), d.get('changePct'),
                          d.get('unit') or '¢/lb', '주간 리포트', d.get('comment')))

    for key, label, sym, note in (('wti', 'WTI Crude', 'CL=F', 'NYMEX'),
                                  ('brent', 'Brent Crude', 'BZ=F', 'Global benchmark')):
        d = q(sym)
        tiles.append(tile(key, label, d.get('price'), d.get('changePct'), 'USD/bbl', note))

    # 'Raw' 는 주간 리포트가 마지막으로 갱신된 날. updatedAt 의 날짜 부분이다.
    return {
        'tiles': [t for t in tiles if t['price'] is not None],
        'schedule': (market or {}).get('schedule') or '',
        'snapshotDate': (market or {}).get('snapshotDateKST') or '',
        'marketDataDate': (market or {}).get('marketDataDate') or '',
        'rawDate': ((weekly or {}).get('updatedAt') or '')[:10],
    }


def main():
    try:
        market = get('/api/market')
        weekly = get('/api/raw-material-update')
    except (urllib.error.URLError, ValueError, OSError) as e:
        # 받아오지 못하면 기존 스냅샷을 그대로 둔다 — 빈 파일로 덮어써서
        # 대시보드가 통째로 비는 편보다 어제 값이 남는 편이 낫다.
        print('가져오기 실패, 기존 스냅샷 유지: %s' % e, file=sys.stderr)
        return 1

    payload = build(market, weekly)
    if not payload['tiles']:
        print('타일이 하나도 없음 — 응답 구조가 바뀌었는지 확인할 것', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = ''
    if os.path.exists(OUT):
        old = io.open(OUT, encoding='utf-8').read()
    new = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if new == old:
        print('변경 없음')
        return 0
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)
    print('%d개 타일 기록 (시장 %s / 주간 %s)'
          % (len(payload['tiles']), payload['marketDataDate'], payload['rawDate']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
