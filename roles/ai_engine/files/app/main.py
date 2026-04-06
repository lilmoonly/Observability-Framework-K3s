import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from prometheus_client import Counter, Gauge, start_http_server
from pyod.models.iforest import IForest
from sklearn.preprocessing import StandardScaler


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("ai-engine")


PROMETHEUS_URL = os.environ["PROMETHEUS_URL"].rstrip("/")
OPENSEARCH_URL = os.environ["OPENSEARCH_URL"].rstrip("/")
OPENSEARCH_USERNAME = os.environ["OPENSEARCH_USERNAME"]
OPENSEARCH_PASSWORD = os.environ["OPENSEARCH_PASSWORD"]
APP_PORT = int(os.getenv("APP_PORT", "8000"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
TRAINING_WINDOW_HOURS = int(os.getenv("TRAINING_WINDOW_HOURS", "24"))
STEP_SECONDS = int(os.getenv("STEP_SECONDS", "300"))
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.9"))
CONTAMINATION = float(os.getenv("CONTAMINATION", "0.05"))
MIN_TRAINING_SAMPLES = int(os.getenv("MIN_TRAINING_SAMPLES", "36"))

REQUEST_TIMEOUT = 30

QUERY_DEFINITIONS = {
    "cluster_cpu_usage_pct": (
        "100 * avg(1 - rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))"
    ),
    "cluster_memory_usage_pct": (
        "100 * (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
    ),
    "running_pods": "sum(kube_pod_status_phase{phase=\"Running\"})",
    "pending_pods": "sum(kube_pod_status_phase{phase=\"Pending\"})",
    "failed_pods": "sum(kube_pod_status_phase{phase=\"Failed\"})",
    "pod_restart_delta_15m": "sum(increase(kube_pod_container_status_restarts_total[15m]))",
}

RUNS_TOTAL = Counter(
    "ai_anomaly_runs_total",
    "Total number of anomaly detection runs attempted.",
)
RUN_FAILURES_TOTAL = Counter(
    "ai_anomaly_run_failures_total",
    "Total number of anomaly detection runs that failed.",
)
EVENTS_TOTAL = Counter(
    "ai_anomaly_events_total",
    "Total number of anomaly events published to OpenSearch.",
)
LAST_SUCCESS_TIMESTAMP = Gauge(
    "ai_anomaly_last_success_timestamp_seconds",
    "Unix timestamp of the most recent successful anomaly detection run.",
)
LAST_RUN_DURATION = Gauge(
    "ai_anomaly_last_run_duration_seconds",
    "Duration of the latest anomaly detection run in seconds.",
)
ANOMALY_FLAG = Gauge(
    "ai_anomaly_detected",
    "Whether the latest evaluated window was classified as anomalous (1/0).",
)
ANOMALY_SCORE = Gauge(
    "ai_anomaly_score",
    "Raw anomaly score from the PyOD model for the latest evaluated window.",
)
ANOMALY_SCORE_NORMALIZED = Gauge(
    "ai_anomaly_score_normalized",
    "Min-max normalized anomaly score from the latest evaluated window.",
)
ANOMALY_THRESHOLD_VALUE = Gauge(
    "ai_anomaly_threshold",
    "Normalized anomaly score threshold used to mark anomalies.",
)
TRAINING_SAMPLE_COUNT = Gauge(
    "ai_anomaly_training_samples",
    "Number of historical samples used in the latest model fit.",
)
AVAILABLE_SAMPLE_COUNT = Gauge(
    "ai_anomaly_available_samples",
    "Number of aligned samples currently available from Prometheus.",
)
REQUIRED_SAMPLE_COUNT = Gauge(
    "ai_anomaly_required_samples",
    "Minimum historical samples required before anomaly evaluation can run.",
)
DETECTOR_READY = Gauge(
    "ai_anomaly_ready",
    "Whether the detector has enough aligned samples to evaluate anomalies (1/0).",
)
FEATURE_VALUE = Gauge(
    "ai_feature_value",
    "Latest feature value used by the anomaly detector.",
    ["feature"],
)
FEATURE_ZSCORE = Gauge(
    "ai_feature_zscore",
    "Latest absolute z-score per feature compared with the training window.",
    ["feature"],
)


def prom_query_range(query, start_ts, end_ts, step_seconds):
    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={
            "query": query,
            "start": int(start_ts),
            "end": int(end_ts),
            "step": step_seconds,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")

    results = payload["data"]["result"]
    if not results:
        raise RuntimeError(f"No Prometheus data returned for query: {query}")

    values = {}
    for result in results:
        for point in result.get("values", []):
            timestamp = int(float(point[0]))
            try:
                numeric_value = float(point[1])
            except ValueError:
                numeric_value = 0.0
            values[timestamp] = values.get(timestamp, 0.0) + numeric_value
    return values


def collect_feature_matrix():
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp())
    start_ts = int((now - timedelta(hours=TRAINING_WINDOW_HOURS)).timestamp())

    series = {}
    for feature_name, query in QUERY_DEFINITIONS.items():
        series[feature_name] = prom_query_range(query, start_ts, end_ts, STEP_SECONDS)

    common_timestamps = None
    for feature_values in series.values():
        timestamps = set(feature_values.keys())
        common_timestamps = timestamps if common_timestamps is None else common_timestamps & timestamps

    if not common_timestamps:
        raise RuntimeError("No aligned timestamps were available across Prometheus queries.")

    ordered_timestamps = sorted(common_timestamps)
    rows = []
    matrix = []
    for timestamp in ordered_timestamps:
        feature_row = {}
        for feature_name in QUERY_DEFINITIONS:
            feature_row[feature_name] = float(series[feature_name][timestamp])
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc),
                "features": feature_row,
            }
        )
        matrix.append([feature_row[name] for name in QUERY_DEFINITIONS])

    return rows, np.asarray(matrix, dtype=float)


def compute_top_contributors(train_raw, latest_raw):
    means = np.mean(train_raw, axis=0)
    stds = np.std(train_raw, axis=0)
    stds = np.where(stds == 0.0, 1.0, stds)
    zscores = np.abs((latest_raw - means) / stds)

    contributors = []
    for index, feature_name in enumerate(QUERY_DEFINITIONS):
        FEATURE_ZSCORE.labels(feature=feature_name).set(float(zscores[index]))
        contributors.append((feature_name, float(zscores[index])))

    contributors.sort(key=lambda item: item[1], reverse=True)
    return [name for name, _ in contributors[:3]]


def publish_document(document):
    index_name = f"ai-anomalies-{document['@timestamp'][:10].replace('-', '.')}"
    response = requests.post(
        f"{OPENSEARCH_URL}/{index_name}/_doc",
        auth=(OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD),
        headers={"Content-Type": "application/json"},
        data=json.dumps(document),
        timeout=REQUEST_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    return index_name


def evaluate_once():
    RUNS_TOTAL.inc()
    started_at = time.monotonic()

    rows, matrix = collect_feature_matrix()
    latest_row = rows[-1]
    AVAILABLE_SAMPLE_COUNT.set(len(rows))
    REQUIRED_SAMPLE_COUNT.set(MIN_TRAINING_SAMPLES + 1)
    for feature_name, feature_value in latest_row["features"].items():
        FEATURE_VALUE.labels(feature=feature_name).set(float(feature_value))

    if len(rows) < MIN_TRAINING_SAMPLES + 1:
        DETECTOR_READY.set(0)
        LAST_RUN_DURATION.set(time.monotonic() - started_at)
        LOG.info(
            "Detector is still warming up: need at least %s aligned samples, found %s.",
            MIN_TRAINING_SAMPLES + 1,
            len(rows),
        )
        return

    train_raw = matrix[:-1]
    latest_raw = matrix[-1]
    scaler = StandardScaler()
    train_scaled = scaler.fit_transform(train_raw)
    latest_scaled = scaler.transform(latest_raw.reshape(1, -1))

    model = IForest(
        contamination=CONTAMINATION,
        n_estimators=100,
        random_state=42,
    )
    model.fit(train_scaled)

    train_scores = model.decision_function(train_scaled)
    latest_score = float(model.decision_function(latest_scaled)[0])
    latest_label = int(model.predict(latest_scaled)[0])
    combined_scores = np.append(train_scores, latest_score)
    min_score = float(np.min(combined_scores))
    max_score = float(np.max(combined_scores))
    normalized_score = 0.0
    if max_score > min_score:
        normalized_score = (latest_score - min_score) / (max_score - min_score)

    top_contributors = compute_top_contributors(train_raw, latest_raw)
    is_anomaly = bool(latest_label == 1 and normalized_score >= ANOMALY_THRESHOLD)

    ANOMALY_FLAG.set(1 if is_anomaly else 0)
    ANOMALY_SCORE.set(latest_score)
    ANOMALY_SCORE_NORMALIZED.set(normalized_score)
    TRAINING_SAMPLE_COUNT.set(len(train_raw))
    DETECTOR_READY.set(1)
    LAST_SUCCESS_TIMESTAMP.set(time.time())

    if is_anomaly:
        document = {
            "@timestamp": latest_row["timestamp"].isoformat(),
            "source": "prometheus",
            "model": "pyod-iforest",
            "is_anomaly": True,
            "anomaly_score": latest_score,
            "normalized_score": normalized_score,
            "reason": (
                "Latest cluster metrics deviated from the recent training window."
            ),
            "top_contributors": top_contributors,
            "features": latest_row["features"],
            "window": {
                "lookback_hours": TRAINING_WINDOW_HOURS,
                "step_seconds": STEP_SECONDS,
            },
        }
        index_name = publish_document(document)
        EVENTS_TOTAL.inc()
        LOG.info("Published anomaly event to OpenSearch index %s", index_name)

    LAST_RUN_DURATION.set(time.monotonic() - started_at)
    LOG.info(
        "Anomaly evaluation complete: anomaly=%s normalized_score=%.3f top_contributors=%s",
        is_anomaly,
        normalized_score,
        ",".join(top_contributors),
    )


def run_forever():
    while True:
        try:
            evaluate_once()
        except Exception as exc:  # noqa: BLE001
            RUN_FAILURES_TOTAL.inc()
            LOG.exception("Anomaly detection run failed: %s", exc)
        time.sleep(CHECK_INTERVAL_SECONDS)


def main():
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    ANOMALY_FLAG.set(0)
    ANOMALY_SCORE.set(0)
    ANOMALY_SCORE_NORMALIZED.set(0)
    ANOMALY_THRESHOLD_VALUE.set(ANOMALY_THRESHOLD)
    TRAINING_SAMPLE_COUNT.set(0)
    AVAILABLE_SAMPLE_COUNT.set(0)
    REQUIRED_SAMPLE_COUNT.set(MIN_TRAINING_SAMPLES + 1)
    DETECTOR_READY.set(0)
    start_http_server(APP_PORT)
    LOG.info("Starting Prometheus metrics server on port %s", APP_PORT)
    thread = threading.Thread(target=run_forever, daemon=True)
    thread.start()
    thread.join()


if __name__ == "__main__":
    main()
