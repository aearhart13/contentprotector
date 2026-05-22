import pickle
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

import database
from config import DATA_DIR, SCORE_WEIGHTS, MIN_TRAINING_SAMPLES
from features import features_to_vector


def _model_path(client_id: int) -> Path:
    return DATA_DIR / f"model_{client_id}.pkl"


def _scaler_path(client_id: int) -> Path:
    return DATA_DIR / f"scaler_{client_id}.pkl"


def heuristic_score(features: dict) -> float:
    if features.get("account_signals") == -1.0:
        return 0.0
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        val = features.get(key, 0.0)
        total += max(val, 0.0) * weight
    return min(total, 1.0)


def _load_model(client_id: int):
    mp, sp = _model_path(client_id), _scaler_path(client_id)
    if mp.exists() and sp.exists():
        with open(mp, "rb") as f: mdl    = pickle.load(f)
        with open(sp, "rb") as f: scaler = pickle.load(f)
        return mdl, scaler
    return None, None


def predict(features: dict, client_id: int) -> tuple:
    """Returns (score 0-1, method) where method is 'ml' or 'heuristic'."""
    model, scaler = _load_model(client_id)
    vec = np.array(features_to_vector(features)).reshape(1, -1)

    if model is not None:
        try:
            prob = float(model.predict_proba(scaler.transform(vec))[0][1])
            return prob, "ml"
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "Client %d model incompatible with current feature vector — "
                "falling back to heuristic until retrained.", client_id
            )
            _model_path(client_id).unlink(missing_ok=True)
            _scaler_path(client_id).unlink(missing_ok=True)

    return heuristic_score(features), "heuristic"


def train(client_id: int, labeled_data: list) -> Optional[dict]:
    if len(labeled_data) < MIN_TRAINING_SAMPLES:
        return None

    X = np.array([features_to_vector(f) for f, _ in labeled_data])
    y = np.array([lbl for _, lbl in labeled_data])

    if len(np.unique(y)) < 2:
        return None

    scaler  = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
    model.fit(X_scaled, y)

    y_pred = model.predict(X_scaled)
    stats  = {
        "num_samples":     len(labeled_data),
        "num_positives":   int(np.sum(y == 1)),
        "accuracy":        float(accuracy_score(y, y_pred)),
        "precision_score": float(precision_score(y, y_pred, zero_division=0)),
        "recall_score":    float(recall_score(y, y_pred, zero_division=0)),
        "f1_score":        float(f1_score(y, y_pred, zero_division=0)),
        "model_type":      "LogisticRegression",
    }

    with open(_model_path(client_id), "wb") as f: pickle.dump(model,  f)
    with open(_scaler_path(client_id), "wb") as f: pickle.dump(scaler, f)

    database.save_model_run(client_id, stats)
    return stats


def model_exists(client_id: int) -> bool:
    return _model_path(client_id).exists()
