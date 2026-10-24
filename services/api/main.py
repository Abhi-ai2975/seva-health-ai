import time
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from packages.config.settings import settings
from packages.observability.telemetry import telemetry_buffer
from services.identity.router import router as auth_router, citizens_router
from services.screening.router import router as screening_router
from services.risk_engine.router import router as risk_router
from services.intervention_engine.router import router as intervention_router
from services.wearable.router import router as wearable_router
from services.clinical.router import router as clinical_router
from services.documents.router import router as documents_router
from services.notifications.router import router as notifications_router
from services.population_intelligence.router import router as population_router
from services.ai_agent.router import router as ai_router
from services.api.fhir_router import router as fhir_router
from services.trajectory.router import router as trajectory_router
from services.health_worker.router import router as health_worker_router
from services.clinical.interop_router import router as interop_router
from agents.router import router as agents_router
from services.identity.privacy_router import router as privacy_router
from services.demo.router import router as demo_router, challenge_router
from packages.observability.context import CorrelationContext, SpanStage
from packages.observability.tracer import tracer
from packages.observability.metrics import metrics
from services.observability.router import router as observability_router, serve_observability_portal
from packages.security.headers import SecureHeadersMiddleware
from packages.security.rate_limiter import RateLimiterMiddleware
from services.jobs.router import router as jobs_router
from packages.queue.manager import job_queue_manager
from services.security.router import router as security_router
from services.performance_reliability.router import router as reliability_router
from services.deployment.router import router as deployment_router
from packages.security.sql_guard import SQLInjectionViolationError
from packages.security.xss_guard import XSSViolationError
from packages.security.path_traversal_guard import PathTraversalViolationError

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="SevaHealth AI Gateway",
    version=settings.APP_VERSION,
    description=(
        "AI-Native Preventive-Health & NCD Early-Warning Decision-Intelligence Platform. "
        "Proposed for the Seva First Innovation Challenge."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# Security Middleware (Headers & Rate Limiting)
app.add_middleware(RateLimiterMiddleware)
app.add_middleware(SecureHeadersMiddleware)

# Cross-Origin Resource Sharing (CORS) - Explicitly configured authorized origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    incoming_corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    incoming_trace_id = request.headers.get("X-Trace-ID")
    platform = request.headers.get("X-Client-Platform", "web")

    corr_id = CorrelationContext.set_correlation_id(incoming_corr_id)
    trace_id = CorrelationContext.set_trace_id(incoming_trace_id)
    CorrelationContext.set_client_platform(platform)
    CorrelationContext.set_current_stage(SpanStage.API)

    start_time = time.time()
    span_name = f"{request.method} {request.url.path}"

    async with tracer.async_span(
        name=span_name,
        stage=SpanStage.API,
        tags={"method": request.method, "path": request.url.path, "platform": platform},
    ) as current_span:
        try:
            response = await call_next(request)
            duration_ms = round((time.time() - start_time) * 1000, 2)

            metrics.record_api_request(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

            await telemetry_buffer.emit(
                event_type="HTTP_REQUEST",
                payload={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
                correlation_id=corr_id,
            )

            response.headers["X-Correlation-ID"] = corr_id
            response.headers["X-Request-ID"] = corr_id
            response.headers["X-Trace-ID"] = trace_id
            response.headers["X-Span-ID"] = current_span.span_id
            return response
        except Exception as exc:
            duration_ms = round((time.time() - start_time) * 1000, 2)
            metrics.record_api_request(request.method, request.url.path, 500, duration_ms)
            metrics.record_error("UNHANDLED_EXCEPTION", "API_GATEWAY", str(exc))
            raise


# Exception Handlers
from fastapi.responses import JSONResponse
from services.risk_engine.base import PhysiologicalValidationError

@app.exception_handler(PhysiologicalValidationError)
async def physiological_validation_exception_handler(request: Request, exc: PhysiologicalValidationError):
    """Ensures input boundary violations immediately return 422 Unprocessable Entity
    without silently producing misleading or ungrounded risk assessments (TC-RSK-005).
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(exc),
            "error_type": "PHYSIOLOGICAL_VALIDATION_ERROR",
            "metric": exc.metric,
            "value": exc.value,
            "unit": exc.unit,
            "allowed_min": exc.allowed_min,
            "allowed_max": exc.allowed_max,
        },
    )


@app.exception_handler(SQLInjectionViolationError)
async def sql_injection_violation_exception_handler(request: Request, exc: SQLInjectionViolationError):
    """Intercepts and rejects SQL injection attempts with 400 Bad Request (TC-SEC-001)."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
            "error_type": "SQL_INJECTION_VIOLATION",
            "parameter": exc.parameter,
            "safe_handling": "Parameter blocked before execution. Parameterized queries preserved.",
        },
    )


@app.exception_handler(XSSViolationError)
async def xss_violation_exception_handler(request: Request, exc: XSSViolationError):
    """Intercepts and rejects active script injection attempts in strict mode with 400 Bad Request (TC-SEC-002)."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
            "error_type": "XSS_VIOLATION",
            "attack_category": exc.attack_category,
            "payload_snippet": exc.payload_snippet,
            "safe_handling": "Active script blocked before execution. Stored values neutralized.",
        },
    )


@app.exception_handler(PathTraversalViolationError)
async def path_traversal_violation_exception_handler(request: Request, exc: PathTraversalViolationError):
    """Intercepts and rejects path traversal and storage boundary escape attempts with 400 Bad Request (TC-SEC-003)."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
            "error_type": "PATH_TRAVERSAL_VIOLATION",
            "safe_handling": "File operation strictly confined to authorized storage root.",
        },
    )


# Mount Static Files (CSS, JS, Assets)
from fastapi.staticfiles import StaticFiles
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Root Web Portal
@app.get("/", include_in_schema=False)
async def serve_web_portal():
    from fastapi.responses import FileResponse
    static_file = static_dir / "index.html"
    if static_file.exists():
        return FileResponse(static_file)
    return {"message": "SevaHealth AI Gateway Online. Visit /docs for Swagger UI."}


# Favicon endpoints
@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon_ico():
    from fastapi.responses import FileResponse, Response
    ico_file = static_dir / "favicon.ico"
    if ico_file.exists():
        return FileResponse(ico_file, media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/favicon.png", include_in_schema=False)
async def serve_favicon_png():
    from fastapi.responses import FileResponse, Response
    png_file = static_dir / "favicon.png"
    if png_file.exists():
        return FileResponse(png_file, media_type="image/png")
    return Response(status_code=204)



# Health and Liveness Probes
@app.get("/health", tags=["System Health"])
async def health_check() -> Dict[str, Any]:
    return {
        "status": "HEALTHY",
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "components": {
            "api_gateway": "ONLINE",
            "identity_service": "ONLINE",
            "risk_engine": "ONLINE",
            "intervention_engine": "ONLINE",
            "ai_agent_runtime": "ONLINE",
            "fhir_r4_layer": "ONLINE",
            "wearable_service": "ONLINE",
            "population_intelligence": "ONLINE",
            "risk_trajectory_engine": "ONLINE",
        }
    }


@app.get("/metrics", tags=["System Health"])
async def metrics_endpoint() -> Dict[str, Any]:
    events = await telemetry_buffer.flush()
    return {
        "collected_events_count": len(events),
        "status": "OPERATIONAL",
    }


# Mount API v1 Modular Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(citizens_router, prefix="/api/v1")
app.include_router(screening_router, prefix="/api/v1")
app.include_router(risk_router, prefix="/api/v1")
app.include_router(trajectory_router, prefix="/api/v1")
app.include_router(intervention_router, prefix="/api/v1")
app.include_router(wearable_router, prefix="/api/v1")
app.include_router(clinical_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(population_router, prefix="/api/v1")
app.include_router(ai_router, prefix="/api/v1")
app.include_router(fhir_router, prefix="/api/v1")
app.include_router(health_worker_router, prefix="/api/v1")
app.include_router(interop_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")
app.include_router(privacy_router, prefix="/api/v1")
app.include_router(demo_router, prefix="/api/v1", include_in_schema=False)
app.include_router(challenge_router, prefix="/api/v1", include_in_schema=False)
app.include_router(observability_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(security_router, prefix="/api/v1")
app.include_router(reliability_router, prefix="/api/v1")
app.include_router(deployment_router, prefix="/api/v1")


@app.on_event("startup")
async def on_app_startup():
    settings.validate_production_security()
    from scripts.seed_data import seed_all_demo_data
    seed_all_demo_data()
    await job_queue_manager.start_workers(concurrency_per_queue=2)


@app.on_event("shutdown")
async def on_app_shutdown():
    await job_queue_manager.stop_workers()


@app.get("/observability", include_in_schema=False)
async def serve_observability_portal_endpoint():
    return await serve_observability_portal()


@app.get("/health-worker-app", include_in_schema=False)
async def serve_health_worker_app():
    from fastapi.responses import FileResponse
    from pathlib import Path
    app_file = Path(__file__).parent.parent.parent / "apps" / "health-worker-web" / "index.html"
    if app_file.exists():
        return FileResponse(app_file)
    return {"message": "Health Worker App is being loaded."}


@app.get("/citizen-app", include_in_schema=False)
async def serve_citizen_app():
    from fastapi.responses import FileResponse
    from pathlib import Path
    app_file = Path(__file__).parent.parent.parent / "apps" / "citizen-mobile" / "index.html"
    if app_file.exists():
        return FileResponse(app_file)
    return {"message": "Citizen Mobile App is being loaded."}


@app.get("/public-health-app", include_in_schema=False)
async def serve_public_health_app():
    from fastapi.responses import FileResponse
    from pathlib import Path
    app_file = Path(__file__).parent.parent.parent / "apps" / "public-health-web" / "index.html"
    if app_file.exists():
        return FileResponse(app_file)
    return {"message": "Public Health Dashboard is being loaded."}


@app.get("/clinician-app", include_in_schema=False)
async def serve_clinician_app():
    from fastapi.responses import FileResponse
    from pathlib import Path
    app_file = Path(__file__).parent.parent.parent / "apps" / "clinician-web" / "copilot_dashboard.html"
    if app_file.exists():
        return FileResponse(app_file)
    return {"message": "Clinician Copilot Dashboard is being loaded."}


