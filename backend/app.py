# Signal Checklist Platform - Backend (Flask)
# Path: backend/app.py

import os
import json
import datetime
import traceback
import requests
import psycopg2
from flask import Flask, request, jsonify
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# Configuration via env
TV_SECRET = os.getenv("TV_SECRET", "replace_me")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/signalsdb")

# Basic DB helper
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def save_signal(payload, checks):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signals (
            instrument, sector, timeframe, signal_time, payload,
            htf_result, key_pass, pattern_pass, confirm_pass, news_pass,
            overall_pass, reason
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (
        payload.get("instrument", "NAS100"),
        payload.get("sector", "Indices"),
        payload.get("timeframe", "15m"),
        datetime.datetime.utcnow(),
        json.dumps(payload),
        checks.get("htf", {}).get("result", "UNKNOWN"),
        checks.get("key", {}).get("pass", False),
        checks.get("pattern", {}).get("pass", False),
        checks.get("confirm", {}).get("pass", False),
        checks.get("news", {}).get("pass", False),
        checks.get("overall", False),
        json.dumps({k: v for k, v in checks.items() if k != "overall"})
    ))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return row[0]

# Simple EMA used for HTF bias detection
def ema(values, period):
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out = [None] * len(values)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out

# HTF bias: EMA50 vs EMA200
def detect_htf_bias(series):
    closes = [c.get("close") for c in series]
    if len(closes) < 200:
        return {"result": "NEUTRAL", "pass": False, "reason": "insufficient HTF history"}
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    last50 = ema50[-1]
    last200 = ema200[-1]
    if last50 is None or last200 is None:
        return {"result": "NEUTRAL", "pass": False, "reason": "ema missing"}
    if last50 > last200:
        return {"result": "BULL", "pass": True, "reason": f"EMA50({last50:.2f}) > EMA200({last200:.2f})"}
    if last50 < last200:
        return {"result": "BEAR", "pass": True, "reason": f"EMA50({last50:.2f}) < EMA200({last200:.2f})"}
    return {"result": "NEUTRAL", "pass": False, "reason": "neutral"}

# Simple swing detection for key levels
def local_swing_points(series, left=3, right=3):
    highs = [s.get("high") for s in series]
    lows = [s.get("low") for s in series]
    swing_highs = []
    swing_lows = []
    for i in range(left, len(series) - right):
        h = highs[i]
        prev_h = highs[i-left:i]
        next_h = highs[i+1:i+1+right]
        if all(v < h for v in prev_h) and all(v < h for v in next_h):
            swing_highs.append({"index": i, "value": h})
        l = lows[i]
        prev_l = lows[i-left:i]
        next_l = lows[i+1:i+1+right]
        if all(v > l for v in prev_l) and all(v > l for v in next_l):
            swing_lows.append({"index": i, "value": l})
    return swing_highs, swing_lows

def detect_key_level(series):
    if len(series) < 30:
        return {"pass": False, "reason": "not enough candles"}
    highs, lows = local_swing_points(series, left=4, right=4)
    levels = [v["value"] for v in highs + lows]
    if not levels:
        return {"pass": False, "reason": "no swing levels detected"}
    last_price = series[-1].get("close")
    nearest = min(levels, key=lambda x: abs(x - last_price))
    dist_pct = abs(nearest - last_price) / last_price
    tol = 0.003
    if dist_pct <= tol:
        return {"pass": True, "reason": f"near key level {nearest:.2f} (dist {(dist_pct*100):.2f}%)"}
    return {"pass": False, "reason": f"nearest {nearest:.2f} dist {(dist_pct*100):.2f}% > {(tol*100):.2f}%"}

# Pattern detection (bullish engulfing, breakout Donchian)
def detect_pattern(series):
    if len(series) < 3:
        return {"pass": False, "reason": "not enough data"}
    prev = series[-2]
    last = series[-1]
    bullish_engulf = (
        prev.get("close") < prev.get("open") and
        last.get("close") > last.get("open") and
        last.get("close") > prev.get("open") and
        last.get("open") < prev.get("close")
    )
    if bullish_engulf:
        return {"pass": True, "reason": "bullish engulfing"}
    lookback = 20
    if len(series) >= lookback:
        recent_highs = [s.get("high") for s in series[-lookback:-1]]
        donchian_high = max(recent_highs)
        if last.get("close") > donchian_high:
            return {"pass": True, "reason": f"breakout above Donchian(20) {donchian_high:.2f}"}
    return {"pass": False, "reason": "no pattern"}

# Confirmation candle
def confirm_candle(series, pattern):
    if not pattern.get("pass", False):
        return {"pass": False, "reason": "pattern failed"}
    if len(series) < 2:
        return {"pass": False, "reason": "insufficient candles"}
    prev = series[-2]
    last = series[-1]
    pattern_high = max(prev.get("high"), series[-3].get("high") if len(series) >= 3 else prev.get("high"))
    slice_for_vol = series[-21:-1] if len(series) >= 21 else series[:-1]
    avg_vol = sum(c.get("volume",0) for c in slice_for_vol) / max(len(slice_for_vol), 1)
    if last.get("close") > pattern_high and last.get("volume",0) > avg_vol * 1.25:
        return {"pass": True, "reason": f"close {last.get('close'):.2f} > patternHigh {pattern_high:.2f}, vol {last.get('volume')} > avg {avg_vol:.1f}"}
    return {"pass": False, "reason": f"confirmation failed"}

# News conflict using NewsAPI (replaceable with Bloomberg blpapi if available)
def news_conflict(instrument):
    if not NEWSAPI_KEY:
        return {"pass": True, "reason": "news provider not configured"}
    now = datetime.datetime.datetime.utcnow()
    q = f"{instrument} OR Nasdaq OR market"
    params = {
        "q": q,
        "apiKey": NEWSAPI_KEY,
        "from": (now - datetime.timedelta(hours=2)).isoformat(),
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 10
    }
    try:
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=8)
        r.raise_for_status()
        articles = r.json().get("articles", [])
    except Exception as e:
        return {"pass": False, "reason": f"newsapi error: {str(e)}"}
    blocked = []
    for a in articles:
        title = (a.get("title") or "").lower()
        if any(w in title for w in ["downgrade","crash","bankrupt","surprise","default","collapse","rate hike"]):
            blocked.append(a.get("title"))
    if blocked:
        return {"pass": False, "reason": f"blocking headlines: {blocked[:3]}"}
    return {"pass": True, "reason": "no blocking headlines"}

# API endpoints
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status":"ok"})

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        token = request.headers.get('X-Webhook-Token') or request.json.get('secret')
        if token != TV_SECRET:
            return jsonify({"error":"invalid token"}), 401
        payload = request.get_json() or {}
        instrument = payload.get('instrument','NAS100')
        timeframe = payload.get('timeframe','15m')

        # TODO: replace these placeholder series with real OHLCV from your data provider
        # HTF series (daily) and LTF series (signal timeframe)
        htf_series = []
        ltf_series = []
        # For safety in the prototype we treat missing data as insufficient and mark checks appropriately
        if not htf_series:
            checks_htf = {"result":"NEUTRAL","pass":False,"reason":"no HTF data"}
        else:
            checks_htf = detect_htf_bias(htf_series)
        checks_key = detect_key_level(ltf_series) if ltf_series else {"pass":False,"reason":"no LTF data"}
        checks_pattern = detect_pattern(ltf_series) if ltf_series else {"pass":False,"reason":"no LTF data"}
        checks_confirm = confirm_candle(ltf_series, checks_pattern) if ltf_series else {"pass":False,"reason":"no LTF data"}
        checks_news = news_conflict(instrument)

        overall = checks_htf.get('pass',False) and checks_key.get('pass',False) and checks_pattern.get('pass',False) and checks_confirm.get('pass',False) and checks_news.get('pass',False)

        checks = {
            'htf': checks_htf,
            'key': checks_key,
            'pattern': checks_pattern,
            'confirm': checks_confirm,
            'news': checks_news,
            'overall': overall
        }

        sid = save_signal(payload, checks)
        return jsonify({'signal_id': sid, 'checks': checks}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/signals', methods=['GET'])
def list_signals():
    limit = int(request.args.get('limit', 200))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, instrument, timeframe, signal_time, overall_pass, reason, created_at FROM signals ORDER BY created_at DESC LIMIT %s', (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.route('/metrics', methods=['GET'])
def metrics():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT count(*)::int as total, sum((overall_pass)::int)::int as passed FROM signals")
    row = cur.fetchone()
    cur.close(); conn.close()
    total = row['total'] if row else 0
    passed = row['passed'] if row else 0
    pass_rate = (passed/total) if total>0 else 0
    return jsonify({'total_signals': total, 'passed': passed, 'pass_rate': pass_rate})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
