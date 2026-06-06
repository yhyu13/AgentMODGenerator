"""Prometheus metrics — surfaced at /metrics for scraping.

The metric names below are part of the deploy contract. If you rename one,
update docs/RUNBOOK.md and the Grafana / Prometheus dashboards that read
them. Don't add metrics that aren't used by a dashboard or an alert.

All counters/histograms are process-local. If you ever run multiple
workers (you don't — see P5 scope), switch to a multiprocess collector
or move the counters into Redis.
"""
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)


# API request metrics — path template used as label so cardinality stays
# bounded (no per-request-id labels).
API_REQUESTS_TOTAL = Counter(
    "sdv_api_requests_total",
    "Total HTTP requests handled by the API.",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

API_REQUEST_DURATION_SECONDS = Histogram(
    "sdv_api_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "path"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)


# Pipeline metrics — recorded from orchestrator/pipeline.py.
PIPELINE_RUNS_TOTAL = Counter(
    "sdv_pipeline_runs_total",
    "Total pipeline runs by terminal status.",
    labelnames=("status",),
    registry=REGISTRY,
)

PIPELINE_T2_SCORE = Histogram(
    "sdv_pipeline_t2_score",
    "Distribution of T2 judge panel scores (0..10).",
    buckets=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
    registry=REGISTRY,
)

PIPELINE_GENERATORS_FAILED_TOTAL = Counter(
    "sdv_pipeline_generators_failed_total",
    "Total generator failures across all pipeline runs.",
    labelnames=("generator",),
    registry=REGISTRY,
)

PIPELINE_GENERATORS_SUCCEEDED_TOTAL = Counter(
    "sdv_pipeline_generators_succeeded_total",
    "Total generator successes across all pipeline runs.",
    labelnames=("generator",),
    registry=REGISTRY,
)


# Health metrics — last-seen status of each dependency.
DEPENDENCY_UP = Gauge(
    "sdv_dependency_up",
    "1 if the dependency was reachable on the last /health/deep probe, 0 otherwise.",
    labelnames=("dependency",),
    registry=REGISTRY,
)


def record_pipeline_run(status: str) -> None:
    PIPELINE_RUNS_TOTAL.labels(status=status).inc()


def record_t2_score(score: int) -> None:
    if 0 <= score <= 10:
        PIPELINE_T2_SCORE.observe(score)


def record_generator_outcome(generator: str, succeeded: bool) -> None:
    if succeeded:
        PIPELINE_GENERATORS_SUCCEEDED_TOTAL.labels(generator=generator).inc()
    else:
        PIPELINE_GENERATORS_FAILED_TOTAL.labels(generator=generator).inc()


def render_metrics() -> tuple[bytes, str]:
    """Render the current registry in Prometheus text exposition format."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
