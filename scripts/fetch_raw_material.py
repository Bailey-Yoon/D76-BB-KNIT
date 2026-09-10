#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 우측 원자재 레일용 스냅샷.

출처를 두 갈래로 나눠 받는다 — 한쪽이 죽어도 나머지 타일은 살아남는다.

  A. 시장 시세 (면화 선물 · WTI · Brent)
     Yahoo Finance 에서 우리가 직접 받는다. 8담당 앱도 결국 같은 Yahoo 를
     쓰므로 중간 단계를 걷어낸 것이다. 실패하면 FRED(유가) → 8담당 순으로 떨어진다.

  B. 주간 원자재 (중국·인도 면화 · PSF · DTY)
     8담당 DAILY MARKET BRIEF 밖에 없다. 화섬 시세는 무료 공개 API 가 존재하지
     않는다 (CCFGroup·ChemAnalyst·EmergingTextiles 전부 구독제. 2026-09-10 확인).
     이쪽이 죽으면 PTA·MEG 선물을 **대리지표**로 명시해 내보낸다 — PSF 원가의
     대부분이 이 둘이라 절대가격은 달라도 방향성은 따라간다.

허브가 브라우저에서 직접 받지 못하는 이유는 그대로다: 위 출처 어디에도
Access-Control-Allow-Origin 이 없다. CORS 가 열린 곳은 World Bank 와
Alpha Vantage 뿐인데 둘 다 월별 지표(1~2개월 지연)라 주간 레일에는 못 쓴다.
그래서 서버에서 받아 data/raw-material.json 으로 커밋하고, 허브는 같은
출처에서 그 파일을 읽는다.

각 타일에 'source' 를 남긴다. 화면에는 안 나오지만 값이 이상할 때
어디서 온 숫자인지 이 파일만 보면 알 수 있어야 한다.
"""

import io
import json
import os
import sys
import time
import datetime
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'raw-material.json')

VERCEL = 'https://newsletter-for-div-8.vercel.app'
TIMEOUT = 25
UA = 'Mozilla/5.0 (compatible; D76-BB-KNIT raw-material snapshot)'

CENT = '¢/lb'
WEEKLY_NOTE = '주간 리포트'
FUT_NOTE = 'ICE Dec-26 선물'


# ── 공통 ────────────────────────────────────────────────────────────────

def fetch(url, headers=None, tries=2):
    """실패하면 None. 한 소스가 죽어도 나머지는 계속 가야 한다."""
    h = {'User-Agent': UA}
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            if i + 1 == tries:
                print('  ! %s -- %s' % (url.split('?')[0], e), file=sys.stderr)
            else:
                time.sleep(1.5)
    return None


def num(v):
    """숫자로 못 바꾸면 None — 0 으로 떨어뜨리면 '변동 없음'으로 잘못 보인다."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # NaN 제외


def pct(now, prev):
    now, prev = num(now), num(prev)
    if now is None or prev is None or prev == 0:
        return None
    return (now - prev) / prev * 100.0


def tile(key, label, price, change, unit, note, source, comment=''):
    return {
        'key': key, 'label': label, 'price': num(price), 'changePct': num(change),
        'unit': unit, 'note': note, 'source': source, 'comment': (comment or '').strip(),
    }


def kst_today():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).strftime('%Y-%m-%d')


# ── A. Yahoo Finance ────────────────────────────────────────────────────

def yahoo(sym):
    """(가격, 전일대비%, 기준일) — 못 받으면 (None, None, None).

    전일대비는 meta 가 아니라 일별 종가 두 개로 직접 계산한다. 선물은
    meta.previousClose 가 None 이고, chartPreviousClose 는 range 시작점의
    종가라서(1mo 면 한 달 전) 그대로 쓰면 한 달치 변동이 하루치로 둔갑한다.
    """
    for host in ('query1', 'query2'):
        raw = fetch('https://%s.finance.yahoo.com/v8/finance/chart/%s'
                    '?range=1mo&interval=1d' % (host, sym), tries=1)
        if not raw:
            continue
        try:
            res = json.loads(raw.decode('utf-8'))['chart']['result'][0]
            meta = res.get('meta') or {}
            ts = res.get('timestamp') or []
            cl = ((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
            pairs = [(t, num(c)) for t, c in zip(ts, cl) if num(c) is not None]
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if len(pairs) < 2:
            continue

        price = num(meta.get('regularMarketPrice'))
        if price is None:
            price = pairs[-1][1]
        # 장중이면 마지막 바가 오늘(진행 중)이고 그 종가는 현재가와 같다.
        # 그때는 그 앞 바를 전일로 본다. 비교를 절대오차로 하면 안 된다 —
        # Yahoo 종가가 float32 라 96.65 가 96.6500015 로 와서 어긋난다.
        same_bar = abs(price - pairs[-1][1]) <= max(1e-6, abs(price) * 1e-4)
        prev = pairs[-2][1] if same_bar else pairs[-1][1]

        when = meta.get('regularMarketTime') or pairs[-1][0]
        day = datetime.datetime.fromtimestamp(
            when, datetime.timezone.utc).strftime('%Y-%m-%d')
        return price, pct(price, prev), day
    return None, None, None


# ── A-fallback. FRED (유가만) ───────────────────────────────────────────

def fred(series):
    """FRED 일별 유가. 무료·키 불필요. EIA 공식 시계열을 그대로 싣는다."""
    raw = fetch('https://fred.stlouisfed.org/graph/fredgraph.csv?id=' + series)
    if not raw:
        return None, None, None
    rows = []
    for line in raw.decode('utf-8', 'replace').splitlines()[1:]:
        parts = line.split(',')
        if len(parts) < 2:
            continue
        v = num(parts[1].strip())
        if v is not None:
            rows.append((parts[0].strip(), v))
    if len(rows) < 2:
        return None, None, None
    return rows[-1][1], pct(rows[-1][1], rows[-2][1]), rows[-1][0]


# ── B. 8담당 DAILY MARKET BRIEF ─────────────────────────────────────────

def vercel(path):
    raw = fetch(VERCEL + path, headers={'Accept': 'application/json'})
    if not raw:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except ValueError:
        print('  ! %s -- JSON not parseable' % path, file=sys.stderr)
        return None


# ── B-fallback. PTA · MEG 선물 (대리지표) ───────────────────────────────

def sina_proxy():
    """PSF/DTY 를 못 받았을 때만 쓴다.

    PSF 원가는 대체로 PTA 0.85 + MEG 0.34 + 가공비로 구성된다. 두 선물이
    오르면 PSF 도 따라 오른다. 절대가격은 PSF 가 아니므로 라벨에 반드시
    '대리지표' 를 남긴다 — 안 그러면 위안/톤 수치를 PSF 시세로 오해한다.
    """
    raw = fetch('https://hq.sinajs.cn/list=nf_TA0,nf_EG0',
                headers={'Referer': 'https://finance.sina.com.cn'})
    if not raw:
        return []
    txt = raw.decode('gbk', 'replace')
    out = []
    specs = (
        ('ptaProxy', 'PTA (PSF 원료)', 'nf_TA0'),
        ('megProxy', 'MEG (PSF 원료)', 'nf_EG0'),
    )
    note = '대리지표 · PSF 미수신'
    cmt = 'PSF 시세를 받지 못해 원료 선물로 대신 표시합니다.'
    for key, label, tag in specs:
        seg = None
        for line in txt.splitlines():
            if tag in line and '="' in line:
                seg = line.split('="', 1)[1].rstrip('";').split(',')
                break
        if not seg or len(seg) < 11:
            continue
        last = num(seg[8]) or num(seg[6])          # 최신가, 없으면 매수호가
        prev = num(seg[10])                        # 전일 정산가
        if last is None or last == 0:
            continue
        out.append(tile(key, label, last, pct(last, prev), 'CNY/t',
                        note, 'sina:' + tag, cmt))
    return out


# ── 조립 ────────────────────────────────────────────────────────────────

def build():
    market = vercel('/api/market')
    weekly = vercel('/api/raw-material-update')
    md = ((market or {}).get('data') or {})
    rm = ((weekly or {}).get('rawMaterials') or {})

    tiles = []
    used = set()
    days = []

    def push(t):
        if t['price'] is None:
            return False
        tiles.append(t)
        used.add(t['source'].split(':')[0])
        return True

    # ① 미국 면화 — ICE Dec-26 선물. Yahoo → 8담당 market → 8담당 weekly
    p, c, d = yahoo('CTZ26.NYB')
    if p is not None:
        days.append(d)
        push(tile('usCotton', 'U.S. Cotton', p, c, CENT, FUT_NOTE,
                  'yahoo:CTZ26.NYB', (rm.get('usCotton') or {}).get('comment')))
    else:
        f = md.get('CTZ26.NYB') or {}
        w = rm.get('usCotton') or {}
        if not push(tile('usCotton', 'U.S. Cotton', f.get('price'), f.get('changePct'),
                         CENT, FUT_NOTE, 'vercel:market', w.get('comment'))):
            push(tile('usCotton', 'U.S. Cotton', w.get('price'), w.get('changePct'),
                      w.get('unit') or CENT, WEEKLY_NOTE, 'vercel:weekly',
                      w.get('comment')))

    # ② 주간 원자재 — 대체 소스 없음
    got_weekly = 0
    for key, label in (('chinaCotton', 'China Cotton'), ('indiaCotton', 'India Cotton'),
                       ('psf', 'PSF'), ('dty', 'DTY')):
        w = rm.get(key) or {}
        if push(tile(key, label, w.get('price'), w.get('changePct'),
                     w.get('unit') or CENT, WEEKLY_NOTE, 'vercel:weekly',
                     w.get('comment'))):
            got_weekly += 1
    if got_weekly == 0:
        proxies = sina_proxy()
        for t in proxies:
            push(t)
        if proxies:
            print('  * weekly raw materials unavailable -- fell back to PTA/MEG proxy',
                  file=sys.stderr)

    # ③ 유가 — Yahoo → FRED → 8담당
    oils = (('wti', 'WTI Crude', 'CL=F', 'DCOILWTICO', 'NYMEX'),
            ('brent', 'Brent Crude', 'BZ=F', 'DCOILBRENTEU', 'Global benchmark'))
    for key, label, sym, series, note in oils:
        p, c, d = yahoo(sym)
        if p is not None:
            days.append(d)
            push(tile(key, label, p, c, 'USD/bbl', note, 'yahoo:' + sym))
            continue
        p, c, d = fred(series)
        if p is not None:
            days.append(d)
            push(tile(key, label, p, c, 'USD/bbl', note + ' · FRED', 'fred:' + series))
            continue
        f = md.get(sym) or {}
        push(tile(key, label, f.get('price'), f.get('changePct'), 'USD/bbl',
                  note, 'vercel:market'))

    # 화면 하단 메타 — 우리가 직접 받은 날짜를 우선한다.
    market_date = max(days) if days else ((market or {}).get('marketDataDate') or '')
    return {
        'tiles': tiles,
        'snapshotDate': kst_today(),
        'marketDataDate': market_date,
        'sources': sorted(used),
        'degraded': got_weekly < 4,
    }


def main():
    payload = build()
    if not payload['tiles']:
        # 빈 파일로 덮어써서 레일이 통째로 비는 것보다 어제 값이 남는 편이 낫다.
        print('no tiles -- keeping previous snapshot', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = io.open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else ''
    new = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if new == old:
        print('no change')
        return 0
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)
    print('%d tiles | sources=%s | market=%s%s'
          % (len(payload['tiles']), '/'.join(payload['sources']),
             payload['marketDataDate'], ' | DEGRADED' if payload['degraded'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
