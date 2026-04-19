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
ROLLING_BASELINE_SAMPLES = int(os.getenv("ROLLING_BASELINE_SAMPLES", "12"))
SUPPRESSION_WINDOW_SECONDS = int(os.getenv("SUPPRESSION_WINDOW_SECONDS", "1800"))

RULE_RESTART_DELTA_THRESHOLD = float(os.getenv("RULE_RESTART_DELTA_THRESHOLD", "5"))
RULE_PENDING_PODS_THRESHOLD = float(os.getenv("RULE_PENDING_PODS_THRESHOLD", "5"))
RULE_FAILED_PODS_THRESHOLD = float(os.getenv("RULE_FAILED_PODS_THRESHOLD", "1"))
RULE_NODE_NOT_READY_THRESHOLD = float(os.getenv("RULE_NODE_NOT_READY_THRESHOLD", "1"))
RULE_NODE_PRESSURE_THRESHOLD = float(os.getenv("RULE_NODE_PRESSURE_THRESHOLD", "1"))
RULE_APISERVER_5XX_RATE_THRESHOLD = float(
    os.getenv("RULE_APISERVER_5XX_RATE_THRESHOLD", "0.1")
)
RULE_INGRESS_5XX_RATE_THRESHOLD = float(
    os.getenv("RULE_INGRESS_5XX_RATE_THRESHOLD", "0.1")
)
SEVERITY_CRITICAL_SCORE = float(os.getenv("SEVERITY_CRITICAL_SCORE", "0.97"))


def with_zero(query):
    return f"({query}) or vector(0)"

QUERY_DEFINITIONS = {
    "cluster_cpu_usage_pct": with_zero(
        '100 * avg(1 - rate(node_cpu_seconds_total{mode="idle"}[5m]))'
    ),
    "cluster_memory_usage_pct": with_zero(
        "100 * (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
    ),
    "cluster_disk_usage_pct": with_zero(
        '100 * (1 - (sum(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs",mountpoint="/",device!~"rootfs"}) / sum(node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs",mountpoint="/",device!~"rootfs"})))'
    ),
    "cluster_network_receive_bytes_per_sec": with_zero(
        'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|flannel.*|cni.*"}[5m]))'
    ),
    "cluster_network_transmit_bytes_per_sec": with_zero(
        'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|flannel.*|cni.*"}[5m]))'
    ),
    "running_pods": with_zero('sum(kube_pod_status_phase{phase="Running"})'),
    "pending_pods": with_zero('sum(kube_pod_status_phase{phase="Pending"})'),
    "failed_pods": with_zero('sum(kube_pod_status_phase{phase="Failed"})'),
    "pod_restart_delta_15m": with_zero(
        "sum(increase(kube_pod_container_status_restarts_total[15m]))"
    ),
    "node_not_ready_count": with_zero(
        'sum(kube_node_status_condition{condition="Ready",status=~"false|unknown"})'
    ),
    "node_memory_pressure_count": with_zero(
        'sum(kube_node_status_condition{condition="MemoryPressure",status="true"})'
    ),
    "node_disk_pressure_count": with_zero(
        'sum(kube_node_status_condition{condition="DiskPressure",status="true"})'
    ),
    "node_pid_pressure_count": with_zero(
        'sum(kube_node_status_condition{condition="PIDPressure",status="true"})'
    ),
    "apiserver_5xx_rate": with_zero(
        'sum(rate(apiserver_request_total{code=~"5.."}[5m]))'
    ),
    "apiserver_p99_latency_seconds": with_zero(
        "histogram_quantile(0.99, sum by (le) (rate(apiserver_request_duration_seconds_bucket[5m])))"
    ),
    "traefik_request_rate": with_zero("sum(rate(traefik_service_requests_total[5m]))"),
    "traefik_5xx_rate": with_zero(
        'sum(rate(traefik_service_requests_total{code=~"5.."}[5m]))'
    ),
    "traefik_p95_latency_seconds": with_zero(
        "histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket[5m])))"
    ),
    "max_namespace_cpu_usage_cores": with_zero(
        'max(sum by (namespace) (rate(container_cpu_usage_seconds_total{namespace!="",container!="POD",container!="",image!=""}[5m])))'
    ),
    "max_namespace_memory_working_set_bytes": with_zero(
        'max(sum by (namespace) (container_memory_working_set_bytes{namespace!="",container!="POD",container!="",image!=""}))'
    ),
    "max_namespace_restarts_15m": with_zero(
        'max(sum by (namespace) (increase(kube_pod_container_status_restarts_total{namespace!=""}[15m])))'
    ),
    "max_pod_cpu_usage_cores": with_zero(
        'max(sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{namespace!="",container!="POD",container!="",image!=""}[5m])))'
    ),
    "max_pod_memory_working_set_bytes": with_zero(
        'max(sum by (namespace,pod) (container_memory_working_set_bytes{namespace!="",container!="POD",container!="",image!=""}))'
    ),
    "max_pod_restarts_15m": with_zero(
        'max(sum by (namespace,pod) (increase(kube_pod_container_status_restarts_total{namespace!=""}[15m])))'
    ),
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
FEATURE_BASELINE = Gauge(
    "ai_feature_baseline",
    "Rolling median baseline per feature used by the anomaly detector.",
    ["feature"],
)
FEATURE_RESIDUAL = Gauge(
    "ai_feature_residual",
    "Residual value per feature after subtracting the rolling baseline.",
    ["feature"],
)
RULE_HIT_COUNT = Gauge(
    "ai_anomaly_rule_hits",
    "Number of rule-based checks triggered by the latest evaluation.",
)
SEVERITY_LEVEL = Gauge(
    "ai_anomaly_severity_level",
    "Severity of the latest evaluation: 0=normal, 2=warning, 3=critical.",
)
LAST_PUBLISHED_TIMESTAMP = Gauge(
    "ai_anomaly_last_published_timestamp_seconds",
    "Unix timestamp of the most recent anomaly event published to OpenSearch.",
)
SUPPRESSED_TOTAL = Counter(
    "ai_anomaly_suppressed_total",
    "Total number of anomaly events suppressed to avoid noisy duplicates.",
)
SUPPRESSED_FLAG = Gauge(
    "ai_anomaly_suppressed",
    "Whether the latest anomaly was suppressed instead of being written (1/0).",
)

LAST_PUBLISHED_EVENT = {
    "signature": None,
    "published_at": 0.0,
    "severity_level": 0,
}


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


def apply_rolling_baseline(rows, raw_matrix):
    baseline_matrix = np.zeros_like(raw_matrix, dtype=float)
    residual_matrix = np.zeros_like(raw_matrix, dtype=float)

    for row_index in range(len(raw_matrix)):
        history_start = max(0, row_index - ROLLING_BASELINE_SAMPLES)
        history = raw_matrix[history_start:row_index]
        if len(history) == 0:
            baseline = raw_matrix[row_index]
        else:
            baseline = np.median(history, axis=0)

        residual = raw_matrix[row_index] - baseline
        baseline_matrix[row_index] = baseline
        residual_matrix[row_index] = residual
        rows[row_index]["baseline"] = {
            feature_name: float(baseline[index])
            for index, feature_name in enumerate(QUERY_DEFINITIONS)
        }
        rows[row_index]["residuals"] = {
            feature_name: float(residual[index])
            for index, feature_name in enumerate(QUERY_DEFINITIONS)
        }

    return baseline_matrix, residual_matrix


def compute_top_contributors(train_model, latest_model):
    means = np.mean(train_model, axis=0)
    stds = np.std(train_model, axis=0)
    stds = np.where(stds == 0.0, 1.0, stds)
    zscores = np.abs((latest_model - means) / stds)

    contributors = []
    for index, feature_name in enumerate(QUERY_DEFINITIONS):
        FEATURE_ZSCORE.labels(feature=feature_name).set(float(zscores[index]))
        contributors.append((feature_name, float(zscores[index])))

    contributors.sort(key=lambda item: item[1], reverse=True)
    return [name for name, _ in contributors[:3]]


def evaluate_rules(features):
    rule_hits = []

    if features["failed_pods"] >= RULE_FAILED_PODS_THRESHOLD:
        rule_hits.append("failed_pods_present")
    if features["pending_pods"] >= RULE_PENDING_PODS_THRESHOLD:
        rule_hits.append("pending_pods_spike")
    if features["pod_restart_delta_15m"] >= RULE_RESTART_DELTA_THRESHOLD:
        rule_hits.append("cluster_restart_spike")
    if features["max_namespace_restarts_15m"] >= RULE_RESTART_DELTA_THRESHOLD:
        rule_hits.append("namespace_restart_hotspot")
    if features["max_pod_restarts_15m"] >= RULE_RESTART_DELTA_THRESHOLD:
        rule_hits.append("pod_restart_hotspot")
    if features["node_not_ready_count"] >= RULE_NODE_NOT_READY_THRESHOLD:
        rule_hits.append("node_not_ready")
    if features["node_memory_pressure_count"] >= RULE_NODE_PRESSURE_THRESHOLD:
        rule_hits.append("node_memory_pressure")
    if features["node_disk_pressure_count"] >= RULE_NODE_PRESSURE_THRESHOLD:
        rule_hits.append("node_disk_pressure")
    if features["node_pid_pressure_count"] >= RULE_NODE_PRESSURE_THRESHOLD:
        rule_hits.append("node_pid_pressure")
    if features["apiserver_5xx_rate"] >= RULE_APISERVER_5XX_RATE_THRESHOLD:
        rule_hits.append("apiserver_5xx_spike")
    if features["traefik_5xx_rate"] >= RULE_INGRESS_5XX_RATE_THRESHOLD:
        rule_hits.append("ingress_5xx_spike")

    return rule_hits


def derive_detection_source(model_triggered, rule_triggered):
    if model_triggered and rule_triggered:
        return "model+rules"
    if model_triggered:
        return "model"
    if rule_triggered:
        return "rules"
    return "baseline"


def derive_severity(normalized_score, rule_hits):
    critical_rules = {
        "failed_pods_present",
        "node_not_ready",
        "node_memory_pressure",
        "node_disk_pressure",
        "node_pid_pressure",
        "apiserver_5xx_spike",
    }

    if not rule_hits and normalized_score < ANOMALY_THRESHOLD:
        return "normal", 0
    if normalized_score >= SEVERITY_CRITICAL_SCORE:
        return "critical", 3
    if any(rule_name in critical_rules for rule_name in rule_hits):
        return "critical", 3
    if len(rule_hits) >= 3:
        return "critical", 3
    return "warning", 2


def build_event_signature(rule_hits, top_contributors):
    rule_part = ",".join(sorted(rule_hits)) if rule_hits else "no-rules"
    contributor_part = ",".join(top_contributors) if top_contributors else "no-contributors"
    return f"{rule_part}|{contributor_part}"


def should_publish_anomaly(signature, severity_level):
    now_ts = time.time()
    last_signature = LAST_PUBLISHED_EVENT["signature"]
    last_published_at = LAST_PUBLISHED_EVENT["published_at"]
    last_severity_level = LAST_PUBLISHED_EVENT["severity_level"]

    within_suppression_window = (
        last_published_at > 0
        and now_ts - last_published_at < SUPPRESSION_WINDOW_SECONDS
    )
    if (
        within_suppression_window
        and signature == last_signature
        and severity_level <= last_severity_level
    ):
        return False

    LAST_PUBLISHED_EVENT["signature"] = signature
    LAST_PUBLISHED_EVENT["published_at"] = now_ts
    LAST_PUBLISHED_EVENT["severity_level"] = severity_level
    return True


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

    rows, raw_matrix = collect_feature_matrix()
    _, model_matrix = apply_rolling_baseline(rows, raw_matrix)
    latest_row = rows[-1]
    AVAILABLE_SAMPLE_COUNT.set(len(rows))
    REQUIRED_SAMPLE_COUNT.set(MIN_TRAINING_SAMPLES + 1)
    for feature_name, feature_value in latest_row["features"].items():
        FEATURE_VALUE.labels(feature=feature_name).set(float(feature_value))
        FEATURE_BASELINE.labels(feature=feature_name).set(
            float(latest_row["baseline"][feature_name])
        )
        FEATURE_RESIDUAL.labels(feature=feature_name).set(
            float(latest_row["residuals"][feature_name])
        )

    if len(rows) < MIN_TRAINING_SAMPLES + 1:
        DETECTOR_READY.set(0)
        RULE_HIT_COUNT.set(0)
        SEVERITY_LEVEL.set(0)
        SUPPRESSED_FLAG.set(0)
        LAST_RUN_DURATION.set(time.monotonic() - started_at)
        LOG.info(
            "Detector is still warming up: need at least %s aligned samples, found %s.",
            MIN_TRAINING_SAMPLES + 1,
            len(rows),
        )
        return

    train_model = model_matrix[:-1]
    latest_model = model_matrix[-1]
    scaler = StandardScaler()
    train_scaled = scaler.fit_transform(train_model)
    latest_scaled = scaler.transform(latest_model.reshape(1, -1))

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

    top_contributors = compute_top_contributors(train_model, latest_model)
    rule_hits = evaluate_rules(latest_row["features"])
    model_triggered = bool(latest_label == 1 and normalized_score >= ANOMALY_THRESHOLD)
    rule_triggered = bool(rule_hits)
    is_anomaly = bool(model_triggered or rule_triggered)
    detection_source = derive_detection_source(model_triggered, rule_triggered)
    severity_name, severity_level = derive_severity(normalized_score, rule_hits)

    ANOMALY_FLAG.set(1 if is_anomaly else 0)
    ANOMALY_SCORE.set(latest_score)
    ANOMALY_SCORE_NORMALIZED.set(normalized_score)
    TRAINING_SAMPLE_COUNT.set(len(train_model))
    DETECTOR_READY.set(1)
    LAST_SUCCESS_TIMESTAMP.set(time.time())
    RULE_HIT_COUNT.set(len(rule_hits))
    SEVERITY_LEVEL.set(severity_level)
    SUPPRESSED_FLAG.set(0)

    if is_anomaly:
        signature = build_event_signature(rule_hits, top_contributors)
        should_publish = should_publish_anomaly(signature, severity_level)
        document = {
            "@timestamp": latest_row["timestamp"].isoformat(),
            "source": "prometheus",
            "model": "pyod-iforest",
            "is_anomaly": True,
            "anomaly_score": latest_score,
            "normalized_score": normalized_score,
            "detection_source": detection_source,
            "severity": severity_name,
            "severity_level": severity_level,
            "model_triggered": model_triggered,
            "rule_triggered": rule_triggered,
            "rule_hits": rule_hits,
            "reason": (
                "Latest cluster metrics deviated from the rolling baseline and/or triggered incident rules."
            ),
            "top_contributors": top_contributors,
            "features": latest_row["features"],
            "baseline": latest_row["baseline"],
            "residuals": latest_row["residuals"],
            "window": {
                "lookback_hours": TRAINING_WINDOW_HOURS,
                "step_seconds": STEP_SECONDS,
                "rolling_baseline_samples": ROLLING_BASELINE_SAMPLES,
            },
        }
        if should_publish:
            index_name = publish_document(document)
            EVENTS_TOTAL.inc()
            LAST_PUBLISHED_TIMESTAMP.set(time.time())
            LOG.info(
                "Published anomaly event to OpenSearch index %s severity=%s source=%s rules=%s",
                index_name,
                severity_name,
                detection_source,
                ",".join(rule_hits) if rule_hits else "none",
            )
        else:
            SUPPRESSED_TOTAL.inc()
            SUPPRESSED_FLAG.set(1)
            LOG.info(
                "Suppressed duplicate anomaly severity=%s source=%s rules=%s",
                severity_name,
                detection_source,
                ",".join(rule_hits) if rule_hits else "none",
            )

    LAST_RUN_DURATION.set(time.monotonic() - started_at)
    LOG.info(
        "Anomaly evaluation complete: anomaly=%s severity=%s normalized_score=%.3f source=%s rules=%s top_contributors=%s",
        is_anomaly,
        severity_name,
        normalized_score,
        detection_source,
        ",".join(rule_hits) if rule_hits else "none",
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
    RULE_HIT_COUNT.set(0)
    SEVERITY_LEVEL.set(0)
    LAST_PUBLISHED_TIMESTAMP.set(0)
    SUPPRESSED_FLAG.set(0)
    for feature_name in QUERY_DEFINITIONS:
        FEATURE_VALUE.labels(feature=feature_name).set(0)
        FEATURE_ZSCORE.labels(feature=feature_name).set(0)
        FEATURE_BASELINE.labels(feature=feature_name).set(0)
        FEATURE_RESIDUAL.labels(feature=feature_name).set(0)
    start_http_server(APP_PORT)
    LOG.info("Starting Prometheus metrics server on port %s", APP_PORT)
    thread = threading.Thread(target=run_forever, daemon=True)
    thread.start()
    thread.join()


if __name__ == "__main__":
    main()
