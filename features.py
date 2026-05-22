import unicodedata
from difflib import SequenceMatcher
from io import BytesIO
from typing import Optional

import requests
from PIL import Image
import imagehash
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Maps common homoglyphs and leet-speak to ASCII so "Rуan" (Cyrillic у) → "ryan"
_HOMOGLYPHS = str.maketrans("аеорсхАЕОРСХ", "aeopexAEOPEX")
_LEET       = str.maketrans("013457@", "oieатsа")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.translate(_HOMOGLYPHS)
    text = text.translate(_LEET)
    return text.lower().strip()


# ── Channel-level features ─────────────────────────────────────────────────────

def name_similarity(candidate: str, client: dict) -> float:
    """Character-level similarity between candidate name and all known target name variants."""
    cn = _normalize(candidate)
    scores = [SequenceMatcher(None, _normalize(v), cn).ratio()
              for v in client.get("name_variants", [])]
    return max(scores) if scores else 0.0


def name_contains_keywords(candidate: str, client: dict) -> float:
    """Fraction of target keywords present in the candidate name (capped at 1.0)."""
    cn = _normalize(candidate)
    hits = sum(1 for kw in client.get("keywords", []) if kw in cn)
    return min(hits / 2.0, 1.0)


def description_similarity(candidate_desc: str, client: dict) -> float:
    """TF-IDF cosine similarity between candidate description and target reference text."""
    if not (candidate_desc and candidate_desc.strip()):
        return 0.0
    reference = " ".join(client.get("keywords", [])) + " " + " ".join(client.get("name_variants", []))
    try:
        vec    = TfidfVectorizer(stop_words="english")
        matrix = vec.fit_transform([_normalize(reference), _normalize(candidate_desc)])
        return float(cosine_similarity(matrix[0], matrix[1])[0][0])
    except Exception:
        return 0.0


def _download_image(url: str) -> Optional[Image.Image]:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def _reference_hash(client_id: int) -> Optional[imagehash.ImageHash]:
    from pathlib import Path
    from config import DATA_DIR
    ref = DATA_DIR / f"reference_avatar_{client_id}.jpg"
    if not ref.exists():
        return None
    try:
        return imagehash.phash(Image.open(ref))
    except Exception:
        return None


def avatar_similarity(thumbnail_url: str, client: dict) -> float:
    """Perceptual hash distance between candidate avatar and the real channel's avatar."""
    if not thumbnail_url:
        return 0.0
    ref = _reference_hash(client["id"])
    if ref is None:
        return 0.0
    img = _download_image(thumbnail_url)
    if img is None:
        return 0.0
    try:
        return max(0.0, 1.0 - (ref - imagehash.phash(img)) / 64.0)
    except Exception:
        return 0.0


def account_signals(published_at: str, subscriber_count: int, channel_id: str, client: dict) -> float:
    """Heuristic score based on account age and size. New + tiny accounts score higher."""
    if channel_id == client.get("channel_id"):
        return -1.0  # sentinel: this is the real channel, never flag it

    score = 0.0
    if published_at:
        from datetime import datetime, timezone
        try:
            created  = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created).days
            if age_days < 30:   score += 0.4
            elif age_days < 180: score += 0.2
            elif age_days < 365: score += 0.1
        except Exception:
            pass

    subs = subscriber_count or 0
    if subs < 1_000:    score += 0.4
    elif subs < 10_000:  score += 0.3
    elif subs < 100_000: score += 0.1

    return min(score, 1.0)


# ── Video / content features ───────────────────────────────────────────────────

def video_title_similarity(candidate_titles: list, reference_titles: list) -> float:
    """Average best-match score between candidate titles and reference titles."""
    if not candidate_titles or not reference_titles:
        return 0.0
    ref_norms = [_normalize(t) for t in reference_titles]
    scores = []
    for ct in candidate_titles[:15]:
        best = max(SequenceMatcher(None, _normalize(ct), rn).ratio() for rn in ref_norms)
        scores.append(best)
    return sum(scores) / len(scores) if scores else 0.0


def content_keyword_score(titles: list, descriptions: list, client: dict) -> float:
    """Fraction of content keyword hits across video titles + descriptions (client-specific)."""
    if not titles:
        return 0.0
    keywords = client.get("content_keywords", [])
    if not keywords:
        return 0.0
    combined = _normalize(" ".join(titles + descriptions))
    hits     = sum(1 for kw in keywords if kw in combined)
    return min(hits / 6.0, 1.0)


def title_copy_score(candidate_titles: list, reference_titles: list) -> float:
    """Fraction of candidate titles that near-exactly match a reference title (≥ 0.88)."""
    if not candidate_titles or not reference_titles:
        return 0.0
    ref_norms = [_normalize(t) for t in reference_titles]
    copies    = 0
    for ct in candidate_titles:
        cn = _normalize(ct)
        if cn in ref_norms:
            copies += 1
            continue
        if any(SequenceMatcher(None, cn, rn).ratio() >= 0.88 for rn in ref_norms):
            copies += 1
    return min(copies / max(len(candidate_titles), 1), 1.0)


# ── Combined extraction ────────────────────────────────────────────────────────

def extract_features(channel: dict, client: dict,
                     candidate_videos: list = None,
                     reference_videos: list = None) -> dict:
    base = {
        "name_similarity":        name_similarity(channel.get("channel_name", ""), client),
        "name_keywords":          name_contains_keywords(channel.get("channel_name", ""), client),
        "description_similarity": description_similarity(channel.get("description", ""), client),
        "avatar_similarity":      avatar_similarity(channel.get("thumbnail_url", ""), client),
        "account_signals":        account_signals(
            channel.get("published_at", ""),
            channel.get("subscriber_count", 0),
            channel.get("channel_id", ""),
            client,
        ),
    }

    if candidate_videos is not None and reference_videos is not None:
        c_titles = [v.get("title", "") for v in candidate_videos]
        c_descs  = [v.get("description", "") for v in candidate_videos]
        r_titles = [v.get("title", "") for v in reference_videos]
        base.update({
            "video_title_similarity": video_title_similarity(c_titles, r_titles),
            "content_keyword_score":  content_keyword_score(c_titles, c_descs, client),
            "title_copy_score":       title_copy_score(c_titles, r_titles),
        })
    else:
        base.update({"video_title_similarity": 0.0, "content_keyword_score": 0.0, "title_copy_score": 0.0})

    return base


def features_to_vector(features: dict) -> list:
    return [
        features.get("name_similarity",        0.0),
        features.get("name_keywords",           0.0),
        features.get("description_similarity",  0.0),
        features.get("avatar_similarity",        0.0),
        max(features.get("account_signals",     0.0), 0.0),
        features.get("video_title_similarity",  0.0),
        features.get("content_keyword_score",   0.0),
        features.get("title_copy_score",        0.0),
    ]
