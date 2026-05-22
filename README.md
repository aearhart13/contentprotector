# ContentProtector

A web app for detecting YouTube channel impersonators. Built for creators who need to monitor for fake channels copying their name, branding, and content.

## What it does

- Searches YouTube for channels matching configurable queries and keywords
- Scores each channel using heuristic signals (name similarity, avatar hash, description, video titles) and an optional ML model trained on your own labeled data
- Lets you review flagged channels, mark false positives, and record confirmed impersonators
- Tracks which channels have been reported to YouTube

## Setup

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

**2. Configure environment**

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 key ([get one here](https://console.cloud.google.com/)) |
| `FLASK_SECRET_KEY` | Random string for Flask session signing |

**3. Run**

```bash
python app.py
```

The app runs at `http://localhost:5000`.

## Workflow

1. **Add a client** — enter the creator's name, YouTube handle, keywords, and search queries
2. **Scan** — hits the YouTube API and scores all matching channels
3. **Review** — triage flagged channels as true or false positives
4. **Train** (optional) — once you have 20+ labeled examples, train a per-client ML model to improve scoring
5. **Report** — export confirmed impersonators and mark them as reported to YouTube

## Project structure

| File | Purpose |
|---|---|
| `app.py` | Flask routes |
| `scraper.py` | YouTube API queries and channel data fetching |
| `features.py` | Heuristic scoring (name similarity, avatar hash, etc.) |
| `model.py` | scikit-learn ML model training and inference |
| `database.py` | SQLite persistence |
| `config.py` | Environment config and scoring weights |
