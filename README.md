# signal-checklist-platform

Prototype Signal Checklist Platform

Quickstart (local using Docker Compose):

1. Copy `.env.example` to `.env` and set variables if desired.
2. Run: docker compose up --build
3. Backend API: http://localhost:8000
   - Health: GET /health
   - Webhook: POST /webhook (header X-Webhook-Token must equal TV_SECRET)
   - Signals: GET /signals
   - Metrics: GET /metrics
4. Frontend dashboard: http://localhost:3000

TradingView alert example (set Webhook URL to https://your-host/webhook):

{
  "secret":"replace_me",
  "instrument":"NAS100",
  "timeframe":"15m",
  "side":"BUY"
}

Render deployment notes:
- Create a Web Service on Render for the backend (use Dockerfile in /backend), set environment variables: DATABASE_URL, TV_SECRET, NEWSAPI_KEY.
- Create a Postgres managed DB on Render and provide DATABASE_URL.
- Create a static or web service for frontend (use Dockerfile in /frontend or serve static files).

Replace placeholder OHLCV and news implementations with production data providers (Polygon, Rithmic, Bloomberg, etc.) before going live.
