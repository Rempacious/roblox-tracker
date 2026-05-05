# 🎮 Roblox Profile Tracker

Monitors Roblox players' online status and sends rich Discord webhook notifications when their status changes.

## Features
- Tracks **Offline → Online → Playing a Game → In Studio** transitions
- Sends **rich Discord embeds** with avatar, game thumbnail, player count, and clickable buttons
- Uses **RoTunnel proxy** — no rate limits, no CORS issues
- Resolves game names, thumbnails, and creator info automatically

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Copy the example config and fill in your values:
```bash
cp config.example.json config.json
```

3. Edit `config.json`:
   - `discord_webhook_url` — your Discord webhook URL
   - `roblox_cookie` — *(optional)* your `.ROBLOSECURITY` cookie, only needed for private profiles
   - `user_ids` — list of Roblox user IDs to track

4. Run:
```bash
python tracker.py
```

## Config Options

| Key | Type | Default | Description |
|---|---|---|---|
| `discord_webhook_url` | string | *required* | Your Discord webhook URL |
| `roblox_cookie` | string | `""` | `.ROBLOSECURITY` cookie (optional) |
| `user_ids` | int[] | *required* | Roblox user IDs to monitor |
| `poll_interval_seconds` | int | `30` | Seconds between checks |
| `notify_on_start` | bool | `false` | Send status on first poll |
