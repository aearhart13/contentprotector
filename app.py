import json
import logging
from pathlib import Path

from flask import (Flask, flash, jsonify, redirect,
                   render_template, request, session, url_for)

import database
import model as mdl
from config import FLASK_SECRET_KEY, MIN_TRAINING_SAMPLES

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

database.init_db()


# ── Client context helper ──────────────────────────────────────────────────────

def _current_client():
    """Return the active client dict, defaulting to the first client."""
    clients = database.get_all_clients()
    if not clients:
        return None
    cid = session.get("client_id", clients[0]["id"])
    match = next((c for c in clients if c["id"] == cid), clients[0])
    session["client_id"] = match["id"]
    return match


def _parse_features(channels: list) -> list:
    for ch in channels:
        raw         = ch.get("features_json")
        ch["features"] = json.loads(raw) if raw else {}
        score          = ch.get("ml_score") or ch.get("heuristic_score") or 0
        ch["score"]      = score
        ch["score_pct"]  = int(score * 100)
        ch["score_level"] = "high" if score > 0.7 else "medium" if score > 0.5 else "low"
    return channels


@app.context_processor
def inject_globals():
    clients = database.get_all_clients()
    client  = _current_client()
    stats   = database.get_stats(client["id"]) if client else {}
    return dict(all_clients=clients, current_client=client, stats=stats)


# ── Client switching ───────────────────────────────────────────────────────────

@app.route("/switch-client/<int:client_id>")
def switch_client(client_id):
    session["client_id"] = client_id
    return redirect(request.referrer or url_for("index"))


@app.route("/clients")
def clients():
    all_clients  = database.get_all_clients()
    client_stats = {c["id"]: database.get_stats(c["id"]) for c in all_clients}
    return render_template("clients.html", all_clients=all_clients, client_stats=client_stats)


@app.route("/clients/add", methods=["GET", "POST"])
def add_client():
    if request.method == "POST":
        name   = request.form.get("name", "").strip()
        handle = request.form.get("handle", "").strip()
        if not name:
            flash("Name is required.", "error")
            return render_template("add_client.html")

        # Try to resolve channel ID from handle
        channel_id = ""
        if handle:
            try:
                from scraper import _build_youtube
                yt   = _build_youtube()
                resp = yt.channels().list(
                    part="snippet", forHandle=handle.lstrip("@")
                ).execute()
                items = resp.get("items", [])
                if items:
                    channel_id = items[0]["id"]
                    flash(f"Resolved channel ID: {channel_id}", "success")
                else:
                    flash("Could not resolve handle — channel ID left blank. You can add it later.", "warning")
            except Exception as e:
                flash(f"Handle resolution failed: {e}", "warning")

        slug = handle.lstrip("@").lower().replace(" ", "-") or name.lower().replace(" ", "-")

        new_id = database.insert_client({
            "slug":          slug,
            "name":          name,
            "channel_id":    channel_id,
            "handle":        handle,
            "keywords":      [w.strip() for w in request.form.get("keywords", "").split(",") if w.strip()],
            "name_variants": [v.strip() for v in request.form.get("name_variants", "").split(",") if v.strip()],
            "search_queries":[q.strip() for q in request.form.get("search_queries", "").split("\n") if q.strip()],
            "content_keywords":[k.strip() for k in request.form.get("content_keywords", "").split(",") if k.strip()],
        })
        session["client_id"] = new_id
        flash(f"Client '{name}' added.", "success")
        return redirect(url_for("index"))

    return render_template("add_client.html")


# ── Main views ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    client  = _current_client()
    recent  = _parse_features(database.get_channels(client["id"], flagged_only=True, limit=10))
    return render_template("index.html", channels=recent)


@app.route("/scan", methods=["GET", "POST"])
def scan():
    client = _current_client()
    if request.method == "POST":
        try:
            from scraper import run_scan
            result = run_scan(client["id"])
            flash(
                f"Scan complete for {client['name']}: "
                f"{result['scanned']} channels analyzed, {result['flagged']} flagged.",
                "success",
            )
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:
            flash(f"Scan failed: {e}", "error")
        return redirect(url_for("review"))
    from config import YOUTUBE_API_KEY
    return render_template("scan.html",
                           api_key_set=bool(YOUTUBE_API_KEY),
                           model_exists=mdl.model_exists(client["id"]))


@app.route("/channels")
def channels():
    client  = _current_client()
    page    = max(1, int(request.args.get("page", 1)))
    flagged = request.args.get("flagged", "") == "1"
    offset  = (page - 1) * 50
    data    = _parse_features(
        database.get_channels(client["id"], flagged_only=flagged, limit=50, offset=offset)
    )
    return render_template("channels.html", channels=data, page=page, flagged=flagged)


@app.route("/review")
def review():
    client   = _current_client()
    channels = _parse_features(
        database.get_channels(client["id"], flagged_only=True, reviewed=False, limit=50)
    )
    for ch in channels:
        ch["videos"] = database.get_channel_videos(ch["channel_id"])[:6]
    return render_template("review.html", channels=channels)


@app.route("/feedback/<channel_id>", methods=["POST"])
def feedback(channel_id):
    client        = _current_client()
    label         = int(request.form.get("label", 0))
    notes         = request.form.get("notes", "")
    feedback_type = "true_positive" if label == 1 else "false_positive"
    database.add_feedback(client["id"], channel_id, label, feedback_type, notes)
    flash("Feedback saved.", "success")
    return redirect(url_for("review"))


@app.route("/false-negative", methods=["POST"])
def false_negative():
    client     = _current_client()
    channel_id = request.form.get("channel_id", "").strip()
    notes      = request.form.get("notes", "")
    if not channel_id:
        flash("Channel ID is required.", "error")
        return redirect(url_for("review"))
    if not database.get_channel(client["id"], channel_id):
        database.upsert_channel(client["id"], {
            "channel_id": channel_id, "channel_name": "Manually reported",
            "is_flagged": 1, "reviewed": 1,
        })
    database.add_feedback(client["id"], channel_id, 1, "false_negative", notes)
    flash(f"Channel {channel_id} recorded as a missed impersonator.", "success")
    return redirect(url_for("review"))


@app.route("/report")
def report():
    client = _current_client()
    with database.get_conn() as conn:
        rows = conn.execute("""
            SELECT c.channel_id, c.channel_name, c.thumbnail_url,
                   c.subscriber_count, c.heuristic_score, c.ml_score,
                   c.reported_to_youtube, c.reported_at,
                   f.notes, f.created_at as labeled_at
            FROM feedback f
            JOIN channels c ON c.channel_id=f.channel_id AND c.client_id=f.client_id
            WHERE f.client_id=? AND f.label=1
            ORDER BY c.reported_to_youtube ASC, f.created_at DESC
        """, [client["id"]]).fetchall()
    channels = _parse_features([dict(r) for r in rows])
    return render_template("report.html", channels=channels)


@app.route("/mark-reported", methods=["POST"])
def mark_reported():
    client = _current_client()
    ids    = request.form.getlist("channel_ids")
    if ids:
        database.mark_reported(client["id"], ids)
        flash(f"{len(ids)} channel(s) marked as reported to YouTube.", "success")
    else:
        flash("No channels selected.", "warning")
    return redirect(url_for("report"))


@app.route("/train", methods=["GET", "POST"])
def train():
    client       = _current_client()
    labeled_data = database.get_labeled_data(client["id"])
    if request.method == "POST":
        result = mdl.train(client["id"], labeled_data)
        if result is None:
            need = MIN_TRAINING_SAMPLES - len(labeled_data)
            msg  = (f"Need {need} more labeled examples."
                    if need > 0 else "Need examples of both classes.")
            flash(msg, "warning")
        else:
            flash(
                f"Model trained! F1={result['f1_score']:.3f}, "
                f"Accuracy={result['accuracy']:.3f}",
                "success",
            )
        return redirect(url_for("train"))

    stats = database.get_stats(client["id"])
    return render_template("train.html",
                           num_labeled=len(labeled_data),
                           min_samples=MIN_TRAINING_SAMPLES,
                           model_exists=mdl.model_exists(client["id"]),
                           stats=stats)


@app.route("/api/stats")
def api_stats():
    client = _current_client()
    return jsonify(database.get_stats(client["id"]))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
