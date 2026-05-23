import json
import logging
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import database
import features as feat
import model as mdl
from config import (
    YOUTUBE_API_KEY, FLAG_THRESHOLD,
    MAX_RESULTS_PER_QUERY, MAX_VIDEOS_PER_CHANNEL,
    REFERENCE_VIDEO_COUNT, REFERENCE_REFRESH_DAYS, DATA_DIR,
)

log = logging.getLogger(__name__)


def _build_youtube():
    if not YOUTUBE_API_KEY:
        raise ValueError(
            "YOUTUBE_API_KEY is not set. Add it to your .env file. "
            "Get a free key at https://console.cloud.google.com/ "
            "(enable YouTube Data API v3)."
        )
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _search_channels(youtube, query: str) -> list:
    try:
        resp = (youtube.search()
                .list(part="snippet", q=query, type="channel", maxResults=MAX_RESULTS_PER_QUERY)
                .execute())
        return resp.get("items", [])
    except HttpError as e:
        log.error("Search error for '%s': %s", query, e)
        return []


def _fetch_channel_details(youtube, channel_ids: list) -> list:
    results = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        try:
            resp = (youtube.channels()
                    .list(part="snippet,statistics,contentDetails", id=",".join(batch))
                    .execute())
            results.extend(resp.get("items", []))
        except HttpError as e:
            log.error("Channel detail error: %s", e)
    return results


def _fetch_playlist_videos(youtube, playlist_id: str, max_results: int) -> list:
    if not playlist_id:
        return []
    try:
        resp = (youtube.playlistItems()
                .list(part="snippet", playlistId=playlist_id, maxResults=min(max_results, 50))
                .execute())
        return resp.get("items", [])
    except HttpError as e:
        log.error("Playlist fetch error (%s): %s", playlist_id, e)
        return []


def _playlist_items_to_dicts(items: list, client_id: int, channel_id: str, is_reference: bool) -> list:
    videos = []
    for item in items:
        s      = item.get("snippet", {})
        vid_id = s.get("resourceId", {}).get("videoId")
        if not vid_id:
            continue
        videos.append({
            "client_id":     client_id,
            "channel_id":    channel_id,
            "video_id":      vid_id,
            "title":         s.get("title", ""),
            "description":   (s.get("description", "") or "")[:500],
            "thumbnail_url": (
                s.get("thumbnails", {}).get("medium", {}).get("url", "")
                or s.get("thumbnails", {}).get("default", {}).get("url", "")
            ),
            "published_at":  s.get("publishedAt", ""),
            "is_reference":  1 if is_reference else 0,
        })
    return videos


def _download_reference_data(youtube, client: dict):
    """Download reference avatar + cache reference videos for a client."""
    client_id   = client["id"]
    channel_id  = client["channel_id"]
    avatar_path = DATA_DIR / f"reference_avatar_{client_id}.jpg"
    need_avatar = not avatar_path.exists()
    need_videos = database.reference_videos_stale(client_id, REFERENCE_REFRESH_DAYS)

    if not need_avatar and not need_videos:
        return
    if not channel_id:
        log.warning("Client %d (%s) has no channel_id — skipping reference download.", client_id, client["name"])
        return

    try:
        resp  = (youtube.channels()
                 .list(part="snippet,contentDetails", id=channel_id)
                 .execute())
        items = resp.get("items", [])
        if not items:
            log.warning("Channel %s not found for client %d.", channel_id, client_id)
            return
        ch = items[0]

        if need_avatar:
            url = (ch["snippet"]["thumbnails"].get("high", {}).get("url")
                   or ch["snippet"]["thumbnails"].get("default", {}).get("url"))
            if url:
                img = feat._download_image(url)
                if img:
                    img.save(avatar_path)
                    log.info("Saved reference avatar for %s", client["name"])

        if need_videos:
            uploads = (ch.get("contentDetails", {})
                         .get("relatedPlaylists", {})
                         .get("uploads", ""))
            if uploads:
                raw   = _fetch_playlist_videos(youtube, uploads, REFERENCE_VIDEO_COUNT)
                vdics = _playlist_items_to_dicts(raw, client_id, channel_id, is_reference=True)
                database.upsert_videos(vdics)
                log.info("Cached %d reference videos for %s", len(vdics), client["name"])
    except Exception as e:
        log.warning("Could not download reference data for %s: %s", client["name"], e)


def _check_takedowns(youtube, client_id: int) -> int:
    """Check reported channels; mark any that no longer exist as taken down.
    Returns the number of newly confirmed takedowns."""
    candidates = database.get_reported_active(client_id)
    if not candidates:
        return 0

    ids = [c["channel_id"] for c in candidates]
    taken_down = []

    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        try:
            resp = (youtube.channels()
                    .list(part="id", id=",".join(batch))
                    .execute())
            found = {item["id"] for item in resp.get("items", [])}
            taken_down.extend(cid for cid in batch if cid not in found)
        except Exception as e:
            log.warning("Takedown check error: %s", e)

    if taken_down:
        database.mark_taken_down(client_id, taken_down)
        log.info("Client %d: %d channel(s) confirmed taken down by YouTube.", client_id, len(taken_down))

    return len(taken_down)


def run_scan(client_id: int) -> dict:
    """Run a full scan for one client. Returns {scanned, flagged}."""
    client_row = database.get_client(client_id)
    if not client_row:
        raise ValueError(f"Client {client_id} not found.")
    client = database.build_client_config(client_row)

    youtube = _build_youtube()
    _download_reference_data(youtube, client)

    seen_ids = {client["channel_id"]}  # Always exclude the real channel
    candidate_ids = []

    for query in client["search_queries"]:
        for item in _search_channels(youtube, query):
            cid = item["snippet"]["channelId"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                candidate_ids.append(cid)

    if not candidate_ids:
        return {"scanned": 0, "flagged": 0}

    channel_details  = _fetch_channel_details(youtube, candidate_ids)
    reference_videos = database.get_reference_videos(client_id)

    scanned = 0
    flagged = 0

    for item in channel_details:
        cid     = item["id"]
        snippet = item.get("snippet", {})
        stats   = item.get("statistics", {})
        uploads = (item.get("contentDetails", {})
                       .get("relatedPlaylists", {})
                       .get("uploads", ""))

        channel_data = {
            "channel_id":       cid,
            "channel_name":     snippet.get("title", ""),
            "description":      snippet.get("description", ""),
            "thumbnail_url":    (snippet.get("thumbnails", {}).get("high",    {}).get("url", "")
                                 or snippet.get("thumbnails", {}).get("default", {}).get("url", "")),
            "subscriber_count": int(stats.get("subscriberCount", 0) or 0),
            "video_count":      int(stats.get("videoCount",       0) or 0),
            "published_at":     snippet.get("publishedAt", ""),
        }

        # Fetch candidate's recent videos
        candidate_videos = []
        if uploads:
            raw = _fetch_playlist_videos(youtube, uploads, MAX_VIDEOS_PER_CHANNEL)
            candidate_videos = _playlist_items_to_dicts(raw, client_id, cid, is_reference=False)
            database.upsert_videos(candidate_videos)

        extracted   = feat.extract_features(channel_data, client, candidate_videos, reference_videos)
        score, stype = mdl.predict(extracted, client_id)
        is_flagged  = 1 if score >= FLAG_THRESHOLD else 0

        if is_flagged:
            flagged += 1

        # Preserve reported flag on re-scan
        existing        = database.get_channel(client_id, cid)
        already_reported = existing and existing.get("reported_to_youtube")

        update = {
            "heuristic_score": score if stype == "heuristic" else None,
            "ml_score":        score if stype == "ml"        else None,
            "features_json":   json.dumps(extracted),
            "is_flagged":      is_flagged,
        }
        if already_reported:
            update["reviewed"] = 1

        channel_data.update(update)
        database.upsert_channel(client_id, channel_data)
        scanned += 1
        log.info("Client %d | %s | score=%.2f flagged=%s videos=%d",
                 client_id, channel_data["channel_name"], score, bool(is_flagged), len(candidate_videos))

    taken_down = _check_takedowns(youtube, client_id)
    return {"scanned": scanned, "flagged": flagged, "taken_down": taken_down}
