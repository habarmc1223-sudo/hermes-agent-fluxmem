#!/usr/bin/env python3
"""
#3 Agent Metrics Dashboard — Prometheus-compatible metrics endpoint.

Tracks: tasks completed, avg latency, success rate, cost per agent.
Exposes HTTP endpoint for Grafana/Prometheus scraping.

Usage: python3 gateway/metrics.py  (standalone on port 9090)
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

STATE_DIR = Path(os.getenv("HERMES_STATE_DIR", Path.home() / ".hermes" / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
METRICS_DB = STATE_DIR / "agent_metrics.jsonl"


def record_metric(agent: str, metric: str, value: float, task_id: str = ""):
    """Record an agent metric."""
    entry = {
        "agent": agent,
        "metric": metric,
        "value": value,
        "task_id": task_id,
        "timestamp": datetime.now().isoformat(),
    }
    with open(METRICS_DB, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_metrics() -> dict:
    """Aggregate metrics from stored data."""
    if not METRICS_DB.exists():
        return {"status": "no_data"}

    data = []
    for line in METRICS_DB.read_text().splitlines():
        try:
            data.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    if not data:
        return {"status": "no_data"}

    agents = set(d["agent"] for d in data)
    result = {"uptime_seconds": int(time.monotonic()), "agents": {}}

    for agent in agents:
        agent_data = [d for d in data if d["agent"] == agent]
        tasks = [d for d in agent_data if d["metric"] == "task_completed"]
        latencies = [d for d in agent_data if d["metric"] == "latency_ms"]
        errors = [d for d in agent_data if d["metric"] == "error"]
        costs = [d for d in agent_data if d["metric"] == "cost_usd"]

        result["agents"][agent] = {
            "tasks_completed": len(tasks),
            "avg_latency_ms": sum(d["value"] for d in latencies) / len(latencies) if latencies else 0,
            "error_count": len(errors),
            "total_cost_usd": round(sum(d["value"] for d in costs), 4),
        }

    return result


def prometheus_format(metrics: dict) -> str:
    """Convert metrics to Prometheus text format."""
    lines = [
        "# HELP hermes_agent_uptime_seconds Agent uptime",
        f"hermes_agent_uptime_seconds {metrics.get('uptime_seconds', 0)}",
    ]
    for agent, data in metrics.get("agents", {}).items():
        lines += [
            f"# HELP hermes_agent_tasks_total Total tasks for {agent}",
            f"hermes_agent_tasks_total{{agent=\"{agent}\"}} {data['tasks_completed']}",
            f"# HELP hermes_agent_latency_ms Avg latency for {agent}",
            f"hermes_agent_latency_ms{{agent=\"{agent}\"}} {data['avg_latency_ms']:.1f}",
            f"# HELP hermes_agent_errors_total Total errors for {agent}",
            f"hermes_agent_errors_total{{agent=\"{agent}\"}} {data['error_count']}",
            f"# HELP hermes_agent_cost_usd Total cost for {agent}",
            f"hermes_agent_cost_usd{{agent=\"{agent}\"}} {data['total_cost_usd']}",
        ]
    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/metrics", "/health"):
            metrics = get_metrics()
            if self.path == "/metrics":
                body = prometheus_format(metrics)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            else:
                body = json.dumps(metrics, ensure_ascii=False, indent=2)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def run_metrics_server(port: int = 9090):
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    print(f"Agent metrics: http://0.0.0.0:{port}/metrics")
    server.serve_forever()


if __name__ == "__main__":
    run_metrics_server()
