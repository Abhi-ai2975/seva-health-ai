"""Comprehensive Metrics Collection Engine for SevaHealth AI.

Captures all 16 required observability domains:
1.  Application log volumes (info, warning, error)
2.  API latency & throughput (percentiles p50, p90, p95, p99)
3.  Errors & exceptions (categorized by component & code)
4.  Database metrics (query latency, active connections, cache hit ratio)
5.  Queue metrics (queue depth, enqueued, dequeued, failures)
6.  Agent execution (duration, reasoning steps, completions)
7.  LLM latency (provider, model, duration)
8.  LLM failures (timeouts, rate-limits, provider crashes)
9.  Token usage (prompt, completion, total)
10. Tool calls (name, duration, success/fail)
11. Risk-engine execution (domain, latency, tier distribution)
12. Model inference (version, latency, batch volume)
13. Wearable sync (device type, biometrics ingested, errors)
14. Document pipeline (OCR/parsing latency, document type, status)
15. Notifications (channel, dispatch latency, deliverability)
16. Audit events (action, actor role, resource type)
"""

import time
import math
import threading
from typing import Dict, Any, List, Optional
from collections import defaultdict


class LatencyTracker:
    """Calculates percentiles and rolling latency distributions."""

    def __init__(self, max_samples: int = 1000):
        self.max_samples = max_samples
        self._samples: List[float] = []
        self._lock = threading.Lock()

    def record(self, latency_ms: float):
        with self._lock:
            if len(self._samples) >= self.max_samples:
                self._samples.pop(0)
            self._samples.append(latency_ms)

    def stats(self) -> Dict[str, float]:
        with self._lock:
            if not self._samples:
                return {"count": 0, "avg": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
            sorted_samples = sorted(self._samples)
            n = len(sorted_samples)

            def get_percentile(p: float) -> float:
                idx = min(int(math.ceil(p * n)) - 1, n - 1)
                return round(sorted_samples[max(0, idx)], 2)

            return {
                "count": n,
                "avg": round(sum(sorted_samples) / n, 2),
                "p50": get_percentile(0.50),
                "p90": get_percentile(0.90),
                "p95": get_percentile(0.95),
                "p99": get_percentile(0.99),
                "max": round(sorted_samples[-1], 2),
            }


class MetricsCollector:
    """Unified telemetry and operational metrics aggregator."""

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()

        # 1. Application Logs
        self.log_counts = defaultdict(int)

        # 2. API Latency & Requests
        self.api_requests_total = 0
        self.api_requests_by_route = defaultdict(int)
        self.api_requests_by_status = defaultdict(int)
        self.api_latency = LatencyTracker()
        self.api_route_latency: Dict[str, LatencyTracker] = defaultdict(LatencyTracker)

        # 3. Errors
        self.errors_total = 0
        self.errors_by_type = defaultdict(int)
        self.errors_by_component = defaultdict(int)

        # 4. Database Metrics
        self.db_queries_total = 0
        self.db_latency = LatencyTracker()
        self.db_queries_by_table = defaultdict(int)
        self.db_active_connections = 4
        self.db_cache_hits = 0
        self.db_cache_misses = 0

        # 5. Queue Metrics
        self.queue_enqueued = defaultdict(int)
        self.queue_processed = defaultdict(int)
        self.queue_failed = defaultdict(int)
        self.queue_latency = LatencyTracker()
        self.queue_depths = defaultdict(int)

        # 6. Agent Execution
        self.agent_executions_total = 0
        self.agent_latency = LatencyTracker()
        self.agent_steps_total = 0
        self.agent_escalations_total = 0

        # 7. LLM Latency
        self.llm_calls_total = 0
        self.llm_latency = LatencyTracker()
        self.llm_calls_by_model = defaultdict(int)

        # 8. LLM Failures
        self.llm_failures_total = 0
        self.llm_failures_by_reason = defaultdict(int)

        # 9. Token Usage
        self.tokens_prompt_total = 0
        self.tokens_completion_total = 0

        # 10. Tool Calls
        self.tool_calls_total = 0
        self.tool_calls_by_name = defaultdict(int)
        self.tool_failures_by_name = defaultdict(int)
        self.tool_latency = LatencyTracker()

        # 11. Risk-Engine Execution
        self.risk_evaluations_total = 0
        self.risk_latency = LatencyTracker()
        self.risk_domain_evaluations = defaultdict(int)
        self.risk_tier_distribution = defaultdict(int)

        # 12. Model Inference
        self.inferences_total = 0
        self.inference_latency = LatencyTracker()
        self.inferences_by_version = defaultdict(int)

        # 13. Wearable Sync
        self.wearable_syncs_total = 0
        self.wearable_records_ingested = 0
        self.wearable_sync_errors = 0
        self.wearable_sync_latency = LatencyTracker()
        self.wearable_sync_by_device = defaultdict(int)

        # 14. Document Pipeline
        self.documents_processed_total = 0
        self.documents_failed_total = 0
        self.document_ocr_latency = LatencyTracker()
        self.documents_by_type = defaultdict(int)

        # 15. Notifications
        self.notifications_sent_total = 0
        self.notifications_failed_total = 0
        self.notifications_latency = LatencyTracker()
        self.notifications_by_channel = defaultdict(int)

        # 16. Audit Events
        self.audit_events_total = 0
        self.audit_by_action = defaultdict(int)
        self.audit_by_role = defaultdict(int)

        # 17. Dashboard Metrics (TC-PERF-002)
        self.dashboard_queries_total = 0
        self.dashboard_latency = LatencyTracker()
        self.dashboard_latency_by_query: Dict[str, LatencyTracker] = defaultdict(LatencyTracker)

    # Record API request
    def record_api_request(self, method: str, path: str, status_code: int, duration_ms: float):
        route_key = f"{method} {path}"
        with self._lock:
            self.api_requests_total += 1
            self.api_requests_by_route[route_key] += 1
            self.api_requests_by_status[str(status_code)] += 1
        self.api_latency.record(duration_ms)
        self.api_route_latency[route_key].record(duration_ms)
        if status_code >= 400:
            self.record_error(
                error_type=f"HTTP_{status_code}",
                component="API_GATEWAY",
                message=f"Request to {route_key} failed with status {status_code}",
            )

    # Record Error
    def record_error(self, error_type: str, component: str, message: str = ""):
        with self._lock:
            self.errors_total += 1
            self.errors_by_type[error_type] += 1
            self.errors_by_component[component] += 1

    # Record DB Query
    def record_db_query(self, operation: str, table: str, duration_ms: float, success: bool = True):
        with self._lock:
            self.db_queries_total += 1
            self.db_queries_by_table[table] += 1
            if not success:
                self.errors_total += 1
                self.errors_by_component["DATABASE"] += 1
        self.db_latency.record(duration_ms)

    def record_db_cache(self, hit: bool):
        with self._lock:
            if hit:
                self.db_cache_hits += 1
            else:
                self.db_cache_misses += 1

    # Record Queue
    def record_queue_enqueue(self, queue_name: str, depth: Optional[int] = None):
        with self._lock:
            self.queue_enqueued[queue_name] += 1
            if depth is not None:
                self.queue_depths[queue_name] = depth
            else:
                self.queue_depths[queue_name] += 1

    def record_queue_process(self, queue_name: str, duration_ms: float, success: bool = True):
        with self._lock:
            if success:
                self.queue_processed[queue_name] += 1
            else:
                self.queue_failed[queue_name] += 1
            if self.queue_depths[queue_name] > 0:
                self.queue_depths[queue_name] -= 1
        self.queue_latency.record(duration_ms)

    # Record Agent Execution
    def record_agent_execution(self, agent_name: str, duration_ms: float, steps: int = 1, escalated: bool = False):
        with self._lock:
            self.agent_executions_total += 1
            self.agent_steps_total += steps
            if escalated:
                self.agent_escalations_total += 1
        self.agent_latency.record(duration_ms)

    # Record LLM
    def record_llm_call(
        self,
        provider: str,
        model: str,
        duration_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        success: bool = True,
        error_reason: Optional[str] = None,
    ):
        with self._lock:
            self.llm_calls_total += 1
            self.llm_calls_by_model[f"{provider}:{model}"] += 1
            self.tokens_prompt_total += prompt_tokens
            self.tokens_completion_total += completion_tokens
            if not success:
                self.llm_failures_total += 1
                reason = error_reason or "UNKNOWN_FAILURE"
                self.llm_failures_by_reason[reason] += 1
        self.llm_latency.record(duration_ms)

    # Record Tool Call
    def record_tool_call(self, tool_name: str, duration_ms: float, success: bool = True):
        with self._lock:
            self.tool_calls_total += 1
            self.tool_calls_by_name[tool_name] += 1
            if not success:
                self.tool_failures_by_name[tool_name] += 1
        self.tool_latency.record(duration_ms)

    # Record Risk Engine
    def record_risk_engine(self, domain: str, duration_ms: float, risk_tier: str = "MODERATE"):
        with self._lock:
            self.risk_evaluations_total += 1
            self.risk_domain_evaluations[domain] += 1
            self.risk_tier_distribution[risk_tier] += 1
        self.risk_latency.record(duration_ms)

    # Record Model Inference
    def record_model_inference(self, model_version: str, duration_ms: float, batch_size: int = 1):
        with self._lock:
            self.inferences_total += batch_size
            self.inferences_by_version[model_version] += batch_size
        self.inference_latency.record(duration_ms)

    # Record Wearable Sync
    def record_wearable_sync(self, device_type: str, records_count: int, duration_ms: float, success: bool = True):
        with self._lock:
            self.wearable_syncs_total += 1
            self.wearable_sync_by_device[device_type] += 1
            if success:
                self.wearable_records_ingested += records_count
            else:
                self.wearable_sync_errors += 1
        self.wearable_sync_latency.record(duration_ms)

    # Record Document Pipeline
    def record_document_pipeline(self, doc_type: str, duration_ms: float, success: bool = True):
        with self._lock:
            self.documents_by_type[doc_type] += 1
            if success:
                self.documents_processed_total += 1
            else:
                self.documents_failed_total += 1
        self.document_ocr_latency.record(duration_ms)

    # Record Notification
    def record_notification(self, channel: str, duration_ms: float, success: bool = True):
        with self._lock:
            self.notifications_by_channel[channel] += 1
            if success:
                self.notifications_sent_total += 1
            else:
                self.notifications_failed_total += 1
        self.notifications_latency.record(duration_ms)

    # Record Audit Event
    def record_audit(self, action: str, actor_role: str, resource_type: str):
        with self._lock:
            self.audit_events_total += 1
            self.audit_by_action[action] += 1
            self.audit_by_role[actor_role] += 1

    # Record Log
    def record_log(self, level: str):
        with self._lock:
            self.log_counts[level.upper()] += 1

    # Record Dashboard Query (TC-PERF-002)
    def record_dashboard_query(self, query_name: str, duration_ms: float):
        with self._lock:
            self.dashboard_queries_total += 1
        self.dashboard_latency.record(duration_ms)
        self.dashboard_latency_by_query[query_name].record(duration_ms)

    def get_summary(self) -> Dict[str, Any]:
        """Provides full snapshot of all operational metrics."""
        uptime_sec = round(time.time() - self.start_time, 2)
        total_cache = self.db_cache_hits + self.db_cache_misses
        cache_hit_rate = round(self.db_cache_hits / total_cache * 100, 2) if total_cache > 0 else 100.0

        return {
            "uptime_seconds": uptime_sec,
            "application_logs": dict(self.log_counts),
            "api": {
                "requests_total": self.api_requests_total,
                "latency_stats": self.api_latency.stats(),
                "requests_by_status": dict(self.api_requests_by_status),
                "top_routes": dict(sorted(self.api_requests_by_route.items(), key=lambda x: x[1], reverse=True)[:10]),
            },
            "errors": {
                "total": self.errors_total,
                "by_type": dict(self.errors_by_type),
                "by_component": dict(self.errors_by_component),
            },
            "database": {
                "queries_total": self.db_queries_total,
                "latency_stats": self.db_latency.stats(),
                "active_connections": self.db_active_connections,
                "cache_hit_rate_pct": cache_hit_rate,
                "queries_by_table": dict(self.db_queries_by_table),
            },
            "queues": {
                "enqueued": dict(self.queue_enqueued),
                "processed": dict(self.queue_processed),
                "failed": dict(self.queue_failed),
                "current_depths": dict(self.queue_depths),
                "latency_stats": self.queue_latency.stats(),
            },
            "agent": {
                "executions_total": self.agent_executions_total,
                "steps_total": self.agent_steps_total,
                "escalations_total": self.agent_escalations_total,
                "latency_stats": self.agent_latency.stats(),
            },
            "llm": {
                "calls_total": self.llm_calls_total,
                "failures_total": self.llm_failures_total,
                "latency_stats": self.llm_latency.stats(),
                "calls_by_model": dict(self.llm_calls_by_model),
                "failures_by_reason": dict(self.llm_failures_by_reason),
                "token_usage": {
                    "prompt_tokens": self.tokens_prompt_total,
                    "completion_tokens": self.tokens_completion_total,
                    "total_tokens": self.tokens_prompt_total + self.tokens_completion_total,
                },
            },
            "tools": {
                "calls_total": self.tool_calls_total,
                "calls_by_name": dict(self.tool_calls_by_name),
                "failures_by_name": dict(self.tool_failures_by_name),
                "latency_stats": self.tool_latency.stats(),
            },
            "risk_engine": {
                "evaluations_total": self.risk_evaluations_total,
                "latency_stats": self.risk_latency.stats(),
                "by_domain": dict(self.risk_domain_evaluations),
                "tier_distribution": dict(self.risk_tier_distribution),
            },
            "model_inference": {
                "inferences_total": self.inferences_total,
                "latency_stats": self.inference_latency.stats(),
                "by_version": dict(self.inferences_by_version),
            },
            "wearable_sync": {
                "syncs_total": self.wearable_syncs_total,
                "records_ingested": self.wearable_records_ingested,
                "errors_total": self.wearable_sync_errors,
                "latency_stats": self.wearable_sync_latency.stats(),
                "by_device": dict(self.wearable_sync_by_device),
            },
            "document_pipeline": {
                "processed_total": self.documents_processed_total,
                "failed_total": self.documents_failed_total,
                "latency_stats": self.document_ocr_latency.stats(),
                "by_type": dict(self.documents_by_type),
            },
            "notifications": {
                "sent_total": self.notifications_sent_total,
                "failed_total": self.notifications_failed_total,
                "latency_stats": self.notifications_latency.stats(),
                "by_channel": dict(self.notifications_by_channel),
            },
            "audit": {
                "events_total": self.audit_events_total,
                "by_action": dict(self.audit_by_action),
                "by_role": dict(self.audit_by_role),
            },
        }


metrics = MetricsCollector()
