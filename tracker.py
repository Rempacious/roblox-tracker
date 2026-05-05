"""
Roblox Profile Tracker
======================
Monitors Roblox players' online status and sends
rich Discord webhook notifications when their status changes.

Tracks: Offline → Online → In Game → In Studio transitions.
"""

from __future__ import annotations

import requests
import time
import json
import os
import sys
from datetime import datetime, timezone

# ──────────────────────────────────────────────
#  CONFIGURATION — Edit config.json instead
# ──────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

PRESENCE_TYPES = {
    0: {"label": "Offline",        "emoji": "🔴", "color": 0x808080},
    1: {"label": "Online",         "emoji": "🟢", "color": 0x00D166},
    2: {"label": "Playing a Game", "emoji": "🎮", "color": 0x5865F2},
    3: {"label": "In Studio",      "emoji": "🔨", "color": 0xFEE75C},
}


def load_config():
    """Load configuration from config.json."""
    if not os.path.exists(CONFIG_PATH):
        print(f"[ERROR] Config file not found: {CONFIG_PATH}")
        print("        Please create config.json — see config.example.json for reference.")
        sys.exit(1)

    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)

    # Validate required fields
    if not cfg.get("discord_webhook_url"):
        print("[ERROR] 'discord_webhook_url' is missing in config.json")
        sys.exit(1)
    if not cfg.get("user_ids") or len(cfg["user_ids"]) == 0:
        print("[ERROR] 'user_ids' list is empty in config.json")
        sys.exit(1)

    return cfg


# ──────────────────────────────────────────────
#  ROBLOX API HELPERS (via RoTunnel proxy)
#  https://devforum.roblox.com/t/3809046
#  Usage: replace 'roblox' with 'rotunnel' in URLs
# ──────────────────────────────────────────────

def get_user_info(user_id: int, session: requests.Session) -> dict | None:
    """Fetch basic user info (username, display name)."""
    try:
        r = session.get(f"https://users.rotunnel.com/v1/users/{user_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch user info for {user_id}: {e}")
    return None


def get_avatar_url(user_id: int, session: requests.Session) -> str | None:
    """Fetch the user's headshot thumbnail URL."""
    try:
        r = session.get(
            "https://thumbnails.rotunnel.com/v1/users/avatar-headshot",
            params={"userIds": user_id, "size": "420x420", "format": "Png", "isCircular": "false"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data and data[0].get("imageUrl"):
                return data[0]["imageUrl"]
    except requests.RequestException:
        pass
    return None


def get_presence(user_ids: list[int], session: requests.Session) -> list[dict]:
    """Fetch presence for a list of user IDs via RoTunnel proxy."""
    try:
        r = session.post(
            "https://presence.rotunnel.com/v1/presence/users",
            json={"userIds": user_ids},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("userPresences", [])
        else:
            print(f"  [WARN] Presence API returned {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        print(f"  [WARN] Presence request failed: {e}")
    return []


def get_game_info(place_id: int, session: requests.Session) -> dict | None:
    """Resolve a placeId → full game info dict with name, thumbnail, players, etc."""
    if not place_id:
        return None
    try:
        # Step 1: placeId → universeId
        r = session.get(
            f"https://apis.rotunnel.com/universes/v1/places/{place_id}/universe",
            timeout=10,
        )
        if r.status_code != 200:
            return None
        universe_id = r.json().get("universeId")
        if not universe_id:
            return None

        # Step 2: universeId → game details
        r2 = session.get(
            f"https://games.rotunnel.com/v1/games?universeIds={universe_id}",
            timeout=10,
        )
        if r2.status_code != 200:
            return None
        games = r2.json().get("data", [])
        if not games:
            return None

        game = games[0]
        root_place_id = game.get("rootPlaceId", place_id)
        game_url = f"https://www.roblox.com/games/{root_place_id}"

        info = {
            "name": game.get("name", "Unknown Game"),
            "description": (game.get("description") or "")[:120],
            "creator_name": game.get("creator", {}).get("name", "Unknown"),
            "playing": game.get("playing", 0),
            "visits": game.get("visits", 0),
            "root_place_id": root_place_id,
            "universe_id": universe_id,
            "game_url": game_url,
            "thumbnail_url": None,
        }

        # Step 3: Fetch game icon/thumbnail
        try:
            r3 = session.get(
                "https://thumbnails.rotunnel.com/v1/games/icons",
                params={"universeIds": universe_id, "size": "512x512", "format": "Png", "isCircular": "false"},
                timeout=10,
            )
            if r3.status_code == 200:
                thumbs = r3.json().get("data", [])
                if thumbs and thumbs[0].get("imageUrl"):
                    info["thumbnail_url"] = thumbs[0]["imageUrl"]
        except requests.RequestException:
            pass

        return info

    except requests.RequestException:
        pass
    return None


# ──────────────────────────────────────────────
#  DISCORD WEBHOOK
# ──────────────────────────────────────────────

def send_discord_webhook(webhook_url: str, embed: dict, components: list | None = None, mention_content: str | None = None) -> str | None:
    """Send an embed to a Discord webhook. Returns the message ID if available."""
    payload = {
        "username": "Roblox Tracker",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Roblox_Logo.svg/1200px-Roblox_Logo.svg.png",
        "embeds": [embed],
    }
    if mention_content:
        payload["content"] = mention_content
        payload["allowed_mentions"] = {"users": [uid for uid in mention_content.replace("<@", "").replace(">", "").split() if uid.isdigit()]}
    if components:
        payload["components"] = components
    try:
        # Use ?wait=true to get the message object back (with its ID)
        r = requests.post(webhook_url + "?wait=true", json=payload, timeout=10)
        if r.status_code in (200, 204):
            print("  [DISCORD] Webhook sent ✓")
            try:
                return r.json().get("id")
            except Exception:
                return None
        else:
            print(f"  [DISCORD] Webhook failed ({r.status_code}): {r.text[:200]}")
    except requests.RequestException as e:
        print(f"  [DISCORD] Webhook error: {e}")
    return None


def edit_discord_webhook(webhook_url: str, message_id: str, embed: dict) -> bool:
    """Edit an existing webhook message by its ID."""
    payload = {"embeds": [embed]}
    try:
        r = requests.patch(
            f"{webhook_url}/messages/{message_id}",
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 204):
            print("  [DISCORD] Heartbeat updated ✓")
            return True
        else:
            print(f"  [DISCORD] Edit failed ({r.status_code}): {r.text[:200]}")
    except requests.RequestException as e:
        print(f"  [DISCORD] Edit error: {e}")
    return False


def _format_number(n: int) -> str:
    """Format large numbers: 1234567 → 1.2M, 12345 → 12.3K."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def build_status_embed(
    username: str,
    display_name: str,
    user_id: int,
    old_status: int,
    new_status: int,
    avatar_url: str | None,
    game_info: dict | None = None,
) -> tuple[dict, list | None]:
    """Build a rich Discord embed + button components for a status change."""
    new_info = PRESENCE_TYPES.get(new_status, PRESENCE_TYPES[0])
    old_info = PRESENCE_TYPES.get(old_status, PRESENCE_TYPES[0])
    profile_url = f"https://www.roblox.com/users/{user_id}/profile"
    components = None

    fields = [
        {
            "name": "Previous Status",
            "value": f"{old_info['emoji']} {old_info['label']}",
            "inline": True,
        },
        {
            "name": "Current Status",
            "value": f"{new_info['emoji']} {new_info['label']}",
            "inline": True,
        },
    ]

    # If in a game, show rich game details
    if new_status == 2 and game_info:
        game_url = game_info.get("game_url", "")
        game_name = game_info.get("name", "Unknown Game")

        fields.append({
            "name": "🎮 Now Playing",
            "value": f"**[{game_name}]({game_url})**",
            "inline": False,
        })
        fields.append({
            "name": "👥 Active Players",
            "value": _format_number(game_info.get("playing", 0)),
            "inline": True,
        })
        fields.append({
            "name": "👀 Total Visits",
            "value": _format_number(game_info.get("visits", 0)),
            "inline": True,
        })

        creator = game_info.get("creator_name")
        if creator:
            fields.append({
                "name": "🛠️ Created By",
                "value": creator,
                "inline": True,
            })

        # Link Button — appears below the embed as a clickable button
        components = [
            {
                "type": 1,  # Action Row
                "components": [
                    {
                        "type": 2,      # Button
                        "style": 5,     # Link
                        "label": "🎮 Open Game Page",
                        "url": game_url,
                    },
                    {
                        "type": 2,
                        "style": 5,
                        "label": "👤 View Profile",
                        "url": profile_url,
                    },
                ],
            }
        ]

    embed = {
        "title": f"{new_info['emoji']}  {display_name} is now {new_info['label']}",
        "description": f"**[@{username}]({profile_url})** (ID: `{user_id}`)",
        "url": profile_url,
        "color": new_info["color"],
        "fields": fields,
        "footer": {
            "text": "Roblox Profile Tracker",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if avatar_url:
        embed["thumbnail"] = {"url": avatar_url}

    # Show game thumbnail as the main image when playing
    if new_status == 2 and game_info and game_info.get("thumbnail_url"):
        embed["image"] = {"url": game_info["thumbnail_url"]}

    return embed, components


# ──────────────────────────────────────────────
#  MAIN TRACKER LOOP
# ──────────────────────────────────────────────

def run_tracker():
    """Main entry point — runs the tracker loop."""
    cfg = load_config()
    webhook_url = cfg["discord_webhook_url"]
    user_ids = cfg["user_ids"]
    poll_interval = cfg.get("poll_interval_seconds", 30)
    notify_on_start = cfg.get("notify_on_start", False)
    mention_ids = cfg.get("mention_user_ids", [])
    mention_content = " ".join(f"<@{mid}>" for mid in mention_ids) if mention_ids else None
    heartbeat_minutes = cfg.get("heartbeat_interval_minutes", 60)

    # Create a session (cookie is optional — only needed for private profiles)
    session = requests.Session()
    if cfg.get("roblox_cookie"):
        session.cookies.set(".ROBLOSECURITY", cfg["roblox_cookie"], domain=".rotunnel.com")

    # ── Pre-fetch user info and avatars ──
    print("╔══════════════════════════════════════════════╗")
    print("║       ROBLOX PROFILE TRACKER — STARTING      ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    user_cache: dict[int, dict] = {}
    for uid in user_ids:
        info = get_user_info(uid, session)
        avatar = get_avatar_url(uid, session)
        if info:
            user_cache[uid] = {
                "username": info.get("name", "Unknown"),
                "display_name": info.get("displayName", "Unknown"),
                "avatar_url": avatar,
            }
            print(f"  ✓ Loaded: {user_cache[uid]['display_name']} (@{user_cache[uid]['username']}) — ID {uid}")
        else:
            user_cache[uid] = {
                "username": f"User_{uid}",
                "display_name": f"User_{uid}",
                "avatar_url": None,
            }
            print(f"  ✗ Could not load info for user ID {uid}")

    print()
    print(f"  Tracking {len(user_ids)} user(s)")
    print(f"  Poll interval: {poll_interval}s")
    print(f"  Webhook: {webhook_url[:50]}...")
    print()

    # ── State: last known presence per user ──
    last_status: dict[int, int] = {}  # user_id → userPresenceType
    first_poll = True
    start_time = datetime.now(timezone.utc)
    poll_count = 0
    last_heartbeat = time.time()
    heartbeat_interval = heartbeat_minutes * 60  # convert to seconds
    heartbeat_message_id = None  # track the heartbeat message for editing

    # ── Loop ──
    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] Polling presence...")

            presences = get_presence(user_ids, session)

            for p in presences:
                uid = p.get("userId")
                new_type = p.get("userPresenceType", 0)
                place_id = p.get("placeId")
                old_type = last_status.get(uid)
                cached = user_cache.get(uid, {})

                status_info = PRESENCE_TYPES.get(new_type, PRESENCE_TYPES[0])
                print(f"  {status_info['emoji']} {cached.get('display_name', uid)}: {status_info['label']}", end="")

                # Resolve game info if playing
                game_info = None
                if new_type == 2 and place_id:
                    game_info = get_game_info(place_id, session)
                    if game_info:
                        print(f" — {game_info['name']} ({_format_number(game_info['playing'])} playing)", end="")
                print()

                # Detect status change (skip first poll unless configured)
                if old_type is not None and new_type != old_type:
                    print(f"  ⚡ STATUS CHANGE: {PRESENCE_TYPES.get(old_type, PRESENCE_TYPES[0])['label']} → {status_info['label']}")
                    embed, components = build_status_embed(
                        username=cached.get("username", "Unknown"),
                        display_name=cached.get("display_name", "Unknown"),
                        user_id=uid,
                        old_status=old_type,
                        new_status=new_type,
                        avatar_url=cached.get("avatar_url"),
                        game_info=game_info,
                    )
                    send_discord_webhook(webhook_url, embed, components, mention_content)
                elif first_poll and notify_on_start:
                    # Send initial status on first poll if configured
                    embed, components = build_status_embed(
                        username=cached.get("username", "Unknown"),
                        display_name=cached.get("display_name", "Unknown"),
                        user_id=uid,
                        old_status=new_type,  # same as new on first poll
                        new_status=new_type,
                        avatar_url=cached.get("avatar_url"),
                        game_info=game_info,
                    )
                    send_discord_webhook(webhook_url, embed, components)

                last_status[uid] = new_type

            first_poll = False
            poll_count += 1

            # ── Heartbeat: periodic "still alive" message ──
            if time.time() - last_heartbeat >= heartbeat_interval:
                uptime = datetime.now(timezone.utc) - start_time
                hours, remainder = divmod(int(uptime.total_seconds()), 3600)
                minutes, secs = divmod(remainder, 60)

                # Format uptime nicely
                if hours > 0:
                    uptime_str = f"**{hours}**h **{minutes}**m **{secs}**s"
                elif minutes > 0:
                    uptime_str = f"**{minutes}**m **{secs}**s"
                else:
                    uptime_str = f"**{secs}**s"

                # Build current status summary
                status_lines = []
                for uid in user_ids:
                    cached = user_cache.get(uid, {})
                    s = last_status.get(uid, 0)
                    info = PRESENCE_TYPES.get(s, PRESENCE_TYPES[0])
                    name = cached.get('display_name', uid)
                    avatar = cached.get('avatar_url', '')
                    profile = f"https://www.roblox.com/users/{uid}/profile"
                    status_lines.append(
                        f"{info['emoji']} **[{name}]({profile})** — {info['label']}"
                    )

                # Next heartbeat timestamp for Discord's relative time
                next_hb_unix = int(time.time()) + heartbeat_interval
                next_hb_str = f"<t:{next_hb_unix}:R>"

                # Pulse bar visual
                pulse_bar = "```\n💚 ━━━━━━━━━━━━━ PULSE ━━━━━━━━━━━━━ 💚\n```"

                heartbeat_embed = {
                    "author": {
                        "name": "🟢 TRACKER ONLINE",
                        "icon_url": "https://em-content.zobj.net/source/telegram/386/green-heart_1f49a.webp",
                    },
                    "title": "💚 Heartbeat — All Systems Operational",
                    "description": pulse_bar + "\n" + "\n".join(status_lines),
                    "color": 0x2ECC71,
                    "thumbnail": {
                        "url": "https://em-content.zobj.net/source/telegram/386/beating-heart_1f493.webp",
                    },
                    "fields": [
                        {"name": "⏱️ Uptime", "value": uptime_str, "inline": True},
                        {"name": "📊 Polls", "value": f"**{_format_number(poll_count)}**", "inline": True},
                        {"name": "👥 Tracking", "value": f"**{len(user_ids)}** user(s)", "inline": True},
                        {"name": "🔄 Next Heartbeat", "value": next_hb_str, "inline": True},
                        {"name": "📡 Interval", "value": f"Every **{heartbeat_minutes}** min", "inline": True},
                        {"name": "🌐 Proxy", "value": "RoTunnel", "inline": True},
                    ],
                    "footer": {
                        "text": "Roblox Profile Tracker • Heartbeat",
                        "icon_url": "https://em-content.zobj.net/source/telegram/386/green-heart_1f49a.webp",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                # Edit existing heartbeat message, or send a new one
                if heartbeat_message_id:
                    success = edit_discord_webhook(webhook_url, heartbeat_message_id, heartbeat_embed)
                    if not success:
                        # Message was deleted or expired — send a new one
                        heartbeat_message_id = send_discord_webhook(webhook_url, heartbeat_embed)
                else:
                    heartbeat_message_id = send_discord_webhook(webhook_url, heartbeat_embed)
                last_heartbeat = time.time()
                print(f"  💚 Heartbeat {'updated' if heartbeat_message_id else 'sent'} (uptime: {hours}h {minutes}m {secs}s)")

            print(f"  Next check in {poll_interval}s...\n")

        except KeyboardInterrupt:
            print("\n[EXIT] Tracker stopped by user.")
            break
        except Exception as e:
            print(f"  [ERROR] Unexpected error: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    run_tracker()
