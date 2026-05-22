import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_PATH = str(DATA_DIR / "contentprotector.db")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

# Default score weights (can be overridden per client in the future)
SCORE_WEIGHTS = {
    "name_similarity":       0.20,
    "name_keywords":         0.08,
    "description_similarity":0.10,
    "avatar_similarity":     0.10,
    "account_signals":       0.10,
    "video_title_similarity":0.18,
    "content_keyword_score": 0.12,
    "title_copy_score":      0.12,
}

FLAG_THRESHOLD          = 0.45
MIN_TRAINING_SAMPLES    = 20
MAX_RESULTS_PER_QUERY   = 50
MAX_VIDEOS_PER_CHANNEL  = 20
REFERENCE_VIDEO_COUNT   = 50
REFERENCE_REFRESH_DAYS  = 7
