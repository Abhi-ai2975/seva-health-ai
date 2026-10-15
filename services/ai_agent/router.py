from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from packages.auth.jwt import get_current_user_token, TokenPayload
from packages.ai_schemas.schemas import LLMRiskExplanationOutput, LLMSOAPSummaryOutput
from services.ai_agent.provider import get_ai_provider, AIServiceUnavailableError
from services.ai_agent.safety import ClinicalSafetyEnforcer
from services.ai_agent.tools import agent_telemetry
from services.ai_agent.prevention_agent import (
    prevention_agent,
    PreventionAgentResponse,
)
from services.store import store

router = APIRouter(prefix="/ai", tags=["AI Agent Runtime & Safety"])


class ChatExplanationRequest(BaseModel):
    citizen_id: str
    user_question: str


class PreventionAgentChatRequest(BaseModel):
    citizen_id: str
    query: str
    language: str = "en"


@router.post("/explain/{citizen_id}", response_model=LLMRiskExplanationOutput)
async def explain_risk_with_ai(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    risk = store.get_latest_risk(citizen_id)
    score = risk.overall_score if risk else 0.50

    # Retrieve grounded observation summary
    obs_list = store.get_citizen_observations(citizen_id)
    obs_dict = {o.code.lower(): o.value for o in obs_list}
    if citizen.birth_date:
        try:
            b_year = int(citizen.birth_date.split("-")[0])
            obs_dict["age"] = datetime.now().year - b_year
        except Exception:
            pass
    if citizen.gender:
        obs_dict["sex"] = getattr(citizen.gender, "value", str(citizen.gender)).lower()

    top_drivers = []
    protective_factors = []
    if risk and risk.top_drivers:
        top_drivers = [
            f"{d.feature_name}: observed {d.observed_value} (target: {d.target_value})"
            for d in risk.top_drivers
        ]
    if risk and risk.protective_factors:
        protective_factors = [
            f"{getattr(p, 'feature_name', None) or getattr(p, 'factor_name', 'Protective Factor')}: observed {p.observed_value}"
            for p in risk.protective_factors
        ]

    # Data limitations audit
    missing, conf, limitations = ClinicalSafetyEnforcer.check_missing_essential_information(obs_dict or {})

    vitals_parts = []
    if "systolic_bp" in obs_dict:
        vitals_parts.append(f"Systolic BP {obs_dict['systolic_bp']:.0f} mmHg")
    if "fasting_glucose" in obs_dict:
        vitals_parts.append(f"Glucose {obs_dict['fasting_glucose']:.0f} mg/dL")
    if "bmi" in obs_dict:
        vitals_parts.append(f"BMI {obs_dict['bmi']:.1f} kg/m²")
    vitals_summary = ", ".join(vitals_parts) if vitals_parts else "Biometrics assessed against ICMR standards."

    try:
        provider = get_ai_provider()
        output = await provider.explain_risk(
            citizen_name=f"{citizen.first_name} {citizen.last_name}",
            vitals_summary=vitals_summary,
            risk_score=score,
            top_drivers=top_drivers or None,
            protective_factors=protective_factors or None,
            observations=obs_dict or None,
            limitations=limitations or None,
        )
    except AIServiceUnavailableError:
        output = LLMRiskExplanationOutput(
            plain_summary=(
                f"AI Explanation Service Offline (TC-AI-006): The automated AI explanation service is temporarily "
                f"unavailable. However, the deterministic risk stratification for {citizen.first_name} {citizen.last_name} "
                f"remains fully preserved with an overall risk score of {int(score * 100)}% ({vitals_summary}). "
                f"This assessment was calculated deterministically by the validated clinical risk engine."
            ),
            key_drivers=top_drivers or ["Deterministic clinical parameters preserved in electronic health record"],
            protective_strengths=protective_factors or ["Validated deterministic calculation active"],
            recommended_focus_area="Consult primary care physician or review deterministic screening dashboard.",
            safety_reminder="Clinical review recommended. Validated deterministic screening assessment.",
        )

    # Enforce safety check
    is_safe, sanitized_summary = ClinicalSafetyEnforcer.audit_ai_response(output.plain_summary)
    output.plain_summary = sanitized_summary

    return output


@router.post("/prevention-agent/chat", response_model=PreventionAgentResponse)
async def chat_with_prevention_agent(
    payload: PreventionAgentChatRequest,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Processes conversational preventive health interactions with grounded data retrieval and bounded clinical safety."""
    citizen = store.get_citizen(payload.citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail=f"Citizen '{payload.citizen_id}' not found")

    try:
        response = await prevention_agent.chat(
            citizen_id=payload.citizen_id,
            user_query=payload.query,
            actor=current_user,
            language=payload.language,
        )
        return response
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent runtime error: {str(exc)}")


@router.post("/prevention-agent/summarize-clinician/{citizen_id}", response_model=LLMSOAPSummaryOutput)
async def summarize_for_clinician(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Generates structured longitudinal SOAP clinical summary for attending physician review."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail=f"Citizen '{citizen_id}' not found")

    try:
        summary = await prevention_agent.summarize_for_clinician(
            citizen_id=citizen_id,
            actor=current_user,
        )
        return summary
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent runtime error: {str(exc)}")


class AsyncAIWorkflowRequest(BaseModel):
    citizen_id: str
    workflow_type: str = Field(default="SOAP_SUMMARY", description="SOAP_SUMMARY | MULTI_AGENT_SYNTHESIS | CARE_PLAN_EVALUATION")


@router.post("/workflow-async", status_code=202)
async def dispatch_ai_workflow_async(
    payload: AsyncAIWorkflowRequest,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Enqueues computationally intensive multi-agent synthesis to background queue without blocking citizen UX."""
    from packages.queue.manager import job_queue_manager
    from packages.queue.models import QueueType, JobPriority

    citizen = store.get_citizen(payload.citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail=f"Citizen '{payload.citizen_id}' not found")

    job = await job_queue_manager.enqueue(
        queue=QueueType.AI_WORKFLOW,
        payload={
            "citizen_id": payload.citizen_id,
            "workflow_type": payload.workflow_type,
            "requester_id": current_user.sub,
        },
        priority=JobPriority.HIGH,
        citizen_id=payload.citizen_id,
        tenant_id=current_user.tenant_id,
    )
    return {
        "status": "QUEUED",
        "job_id": job.id,
        "citizen_id": payload.citizen_id,
        "workflow_type": payload.workflow_type,
        "message": "AI multi-agent workflow queued for background processing.",
        "status_url": f"/api/v1/jobs/{job.id}",
    }


@router.get("/prevention-agent/tools")
def list_prevention_agent_tools():
    """Lists available authorized tools, descriptions, and grounding data sources."""
    return {
        "tools": [
            {
                "name": "get_patient_profile",
                "description": "Retrieves demographic and geographical context for the patient.",
                "source": "PostgreSQL Citizen Demographics",
                "auth_required": True,
            },
            {
                "name": "get_latest_vitals",
                "description": "Retrieves latest blood pressure, BMI, waist circumference, and heart rate observations.",
                "source": "openEHR / PostgreSQL Clinical Observations",
                "auth_required": True,
            },
            {
                "name": "get_recent_labs",
                "description": "Retrieves laboratory diagnostic markers including fasting glucose, HbA1c, and lipid profile.",
                "source": "openEHR Diagnostic Labs",
                "auth_required": True,
            },
            {
                "name": "get_risk_assessment",
                "description": "Retrieves composite NCD risk assessment, domain scores, and SHAP-style factor contributions.",
                "source": "SevaHealth NCD Risk Engine",
                "auth_required": True,
            },
            {
                "name": "get_risk_trajectory",
                "description": "Retrieves longitudinal trajectory comparison (current vs previous vs baseline) with non-causal attribution.",
                "source": "SevaHealth Longitudinal Trajectory Engine",
                "auth_required": True,
            },
            {
                "name": "get_intervention_plan",
                "description": "Retrieves active 30-day prevention care plan, tasks, and adherence rate.",
                "source": "SevaHealth Prevention Intervention Engine",
                "auth_required": True,
            },
            {
                "name": "get_wearable_summary",
                "description": "Retrieves 7-day rolling baselines for resting HR, HRV, and daily steps from smartwatches.",
                "source": "Open Wearables Ingestion Layer",
                "auth_required": True,
            },
            {
                "name": "get_medication_list",
                "description": "Retrieves active prescribed medications with strict non-alteration safety boundary.",
                "source": "Clinical Medication Store",
                "auth_required": True,
            },
            {
                "name": "get_clinical_history",
                "description": "Retrieves past screening sessions, encounters, and clinician notes.",
                "source": "Clinical History Repository",
                "auth_required": True,
            },
            {
                "name": "create_checkin",
                "description": "Records citizen daily prevention micro-habit check-in.",
                "source": "Intervention Tracking Store",
                "auth_required": True,
            },
            {
                "name": "create_followup",
                "description": "Schedules routine or targeted follow-up with ASHA worker or PHC.",
                "source": "Care Coordination System",
                "auth_required": True,
            },
            {
                "name": "create_alert",
                "description": "Generates priority or emergency clinical alert for red-flag biometrics.",
                "source": "Clinical Alerting Service",
                "auth_required": True,
            },
            {
                "name": "request_clinician_review",
                "description": "Escalates patient context to clinician triage review queue.",
                "source": "Primary Care Triage Service",
                "auth_required": True,
            },
        ]
    }


@router.get("/prevention-agent/logs/{citizen_id}")
def get_agent_audit_logs(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Retrieves forensic audit trail of all agent decisions and executed tool calls for a citizen."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")
    logs = agent_telemetry.get_logs_for_citizen(citizen_id)
    return {"citizen_id": citizen_id, "total_events": len(logs), "events": logs}


class ToolInvocationPayload(BaseModel):
    tool_name: str
    citizen_id: str
    arguments: Optional[Dict[str, Any]] = None


@router.post("/prevention-agent/invoke-tool")
def invoke_agent_tool_endpoint(
    payload: ToolInvocationPayload,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Direct AI tool execution interface with independent authorization enforcement (TC-AI-005)."""
    from services.ai_agent.tools import invoke_agent_tool
    try:
        res = invoke_agent_tool(
            tool_name=payload.tool_name,
            citizen_id=payload.citizen_id,
            actor=current_user,
            arguments=payload.arguments,
        )
        return {"status": "SUCCESS", "tool_name": payload.tool_name, "result": res}
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/provider/toggle")
def toggle_ai_provider(
    enabled: bool,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Toggles AI provider availability status for TC-AI-006 fault tolerance testing."""
    from services.ai_agent.provider import set_ai_provider_enabled, is_ai_provider_enabled
    set_ai_provider_enabled(enabled)
    return {"status": "SUCCESS", "ai_provider_enabled": is_ai_provider_enabled()}


@router.get("/provider/status")
def get_ai_provider_status(
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Retrieves current AI provider availability status."""
    from services.ai_agent.provider import is_ai_provider_enabled
    return {"ai_provider_enabled": is_ai_provider_enabled()}

