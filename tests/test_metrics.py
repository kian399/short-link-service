import re

from app import metrics


def test_counter_and_labels():
    metrics.inc("demo_total", {"a": "b"})
    metrics.inc("demo_total", {"a": "b"}, 2)
    text = metrics.render()
    assert "# TYPE demo_total counter" in text
    assert 'demo_total{a="b"} 3' in text


def test_histogram_is_cumulative_and_consistent():
    for value in (0.0005, 0.002, 0.2):
        metrics.observe("demo_seconds", value)
    text = metrics.render()

    assert 'demo_seconds_bucket{le="+Inf"} 3' in text
    assert "demo_seconds_count 3" in text

    buckets = [
        (float(le), int(count))
        for le, count in re.findall(r'demo_seconds_bucket\{le="([\d.]+)"\} (\d+)', text)
    ]
    counts = [c for _, c in buckets]
    assert counts == sorted(counts), "桶计数必须单调不减"
    assert counts[-1] <= 3


def test_gauge_render():
    metrics.set_gauge("demo_gauge", 42)
    text = metrics.render()
    assert "# TYPE demo_gauge gauge" in text
    assert "demo_gauge 42" in text


def test_metrics_endpoint_exposes_http_metrics(client):
    client.get("/healthz")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert 'path="/healthz"' in response.text
