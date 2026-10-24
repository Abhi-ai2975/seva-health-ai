"""Automated Verification Suite for UX Refinement (PROMPT 26).

Verifies the user experience architecture across all three core front-ends:
1. Citizen Mobile Dashboard (/citizen-app)
   - Mobile-first, low cognitive load, plain language, accessible
   - Large touch targets (>= 48px)
   - Multilingual readiness (en, hi, kn)
   - Direct answers to the 4 citizen questions:
     1. How am I doing? (#question-how-doing)
     2. What changed? (#question-what-changed)
     3. What should I do today? (#question-what-today)
     4. Do I need professional help? (#question-need-help)
   - Immediate next actions & emergency escalate (108, ASHA, PHC)

2. Clinician Copilot Dashboard (/clinician-app)
   - Clinical ergonomics, draft governance, patient switcher
   - Direct answers to the 5 clinician questions:
     1. Who needs attention? (#who-needs-attention)
     2. What changed? (#what-changed)
     3. Why is risk changing? (#why-risk-changing)
     4. What information is missing? (#what-information-missing)
     5. What action should I consider? (#what-action-to-consider)
   - Longitudinal delta comparison & SHAP feature attribution
   - Missing data checklist & 1-click lab orders
   - Clinician review actions (Accept, Edit, Reject, Prescribe, Escalate)

3. Public-Health Command Center (/public-health-app)
   - Population-level surveillance, differential privacy k >= 10
   - Strategic 5-question executive decision navigator
   - Direct answers to the 5 public health questions:
     1. Where is risk concentrated? (#q1-where-concentrated)
     2. Which populations are affected? (#q2-populations-affected)
     3. Are interventions working? (#q3-interventions-working)
     4. Who has not completed follow-up? (#q4-who-not-completed-followup)
     5. What should the program prioritize? (#q5-program-prioritize)
   - Overdue follow-up surveillance & ASHA outreach dispatch batch
   - Top 5 AI policy resource allocation directives
"""

import pytest
from starlette.testclient import TestClient
from pathlib import Path

from services.api.main import app

client = TestClient(app)


class TestCitizenMobileUX:
    """Tests for Citizen Mobile Dashboard (/citizen-app)."""

    def test_citizen_app_endpoint_serves_html(self):
        resp = client.get("/citizen-app")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert len(resp.text) > 1000

    def test_citizen_answers_all_four_mandatory_questions(self):
        resp = client.get("/citizen-app")
        html = resp.text

        # 1. How am I doing?
        assert "question-how-doing" in html
        assert "HOW AM I DOING?" in html or "How Am I Doing" in html

        # 2. What changed?
        assert "question-what-changed" in html
        assert "WHAT CHANGED?" in html or "What Changed" in html

        # 3. What should I do today?
        assert "question-what-today" in html
        assert "WHAT SHOULD I DO TODAY?" in html or "What Should I Do Today" in html

        # 4. Do I need professional help?
        assert "question-need-help" in html
        assert "DO I NEED PROFESSIONAL HELP?" in html or "Do I Need Professional Help" in html

    def test_citizen_touch_target_accessibility(self):
        resp = client.get("/citizen-app")
        html = resp.text

        # Touch targets must enforce min-height: 48px for mobile accessibility
        assert "min-height: 48px" in html or "min-height:48px" in html

    def test_citizen_multilingual_and_audio_support(self):
        resp = client.get("/citizen-app")
        html = resp.text

        # Multilingual readiness: English, Hindi, Kannada
        assert "btn-lang-en" in html
        assert "btn-lang-hi" in html
        assert "btn-lang-kn" in html

        # Audio read-aloud via Web Speech API
        assert "speakText" in html or "SpeechSynthesisUtterance" in html

    def test_citizen_clear_next_action_and_emergency_triage(self):
        resp = client.get("/citizen-app")
        html = resp.text

        # Immediate escalation options
        assert "callEmergency108" in html
        assert "callAshaWorker" in html
        assert "bookPhcVisit" in html


class TestClinicianCopilotUX:
    """Tests for Clinician Copilot Dashboard (/clinician-app)."""

    def test_clinician_app_endpoint_serves_html(self):
        resp = client.get("/clinician-app")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert len(resp.text) > 1000

    def test_clinician_answers_all_five_mandatory_questions(self):
        resp = client.get("/clinician-app")
        html = resp.text

        # 1. Who needs attention?
        assert "who-needs-attention" in html
        assert "WHO NEEDS ATTENTION?" in html

        # 2. What changed?
        assert "what-changed" in html
        assert "WHAT CHANGED?" in html

        # 3. Why is risk changing?
        assert "why-risk-changing" in html
        assert "WHY IS RISK CHANGING?" in html

        # 4. What information is missing?
        assert "what-information-missing" in html
        assert "WHAT INFORMATION IS MISSING?" in html

        # 5. What action should I consider?
        assert "what-action-to-consider" in html
        assert "WHAT ACTION SHOULD I CONSIDER?" in html

    def test_clinician_patient_switcher_and_personas(self):
        resp = client.get("/clinician-app")
        html = resp.text

        # Multi-patient triage queue
        assert "switchPatient" in html
        assert "Ramesh Patel" in html
        assert "Suresh Kumar" in html
        assert "Priya Sharma" in html
        assert "Sunita Devi" in html

    def test_clinician_explainable_attribution_and_deltas(self):
        resp = client.get("/clinician-app")
        html = resp.text

        # Feature attribution / SHAP waterfall & Longitudinal progression
        assert "attributionBarsContainer" in html or "SHAP" in html
        assert "deltaTableBody" in html or "Biomarker" in html
        assert "WHAT CHANGED?" in html
        assert "WHY IS RISK CHANGING?" in html

    def test_clinician_governance_actions(self):
        resp = client.get("/clinician-app")
        html = resp.text

        # Review & decision pathways
        assert "Accept (Verify)" in html
        assert "Edit &amp; Sign" in html or "Edit & Sign" in html
        assert "Reject" in html
        assert "Prescribe Care Plan" in html
        assert "Issue Specialist Referral" in html
        assert "Escalate Urgency" in html


class TestPublicHealthCommandCenterUX:
    """Tests for Public-Health Command Center (/public-health-app)."""

    def test_public_health_app_endpoint_serves_html(self):
        resp = client.get("/public-health-app")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert len(resp.text) > 1000

    def test_public_health_answers_all_five_mandatory_questions(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # 1. Where is risk concentrated?
        assert "q1-where-concentrated" in html
        assert "WHERE IS RISK CONCENTRATED?" in html

        # 2. Which populations are affected?
        assert "q2-populations-affected" in html
        assert "WHICH POPULATIONS ARE AFFECTED?" in html

        # 3. Are interventions working?
        assert "q3-interventions-working" in html
        assert "ARE INTERVENTIONS WORKING?" in html

        # 4. Who has not completed follow-up?
        assert "q4-who-not-completed-followup" in html
        assert "WHO HAS NOT COMPLETED FOLLOW-UP?" in html

        # 5. What should the program prioritize?
        assert "q5-program-prioritize" in html
        assert "WHAT SHOULD THE PROGRAM PRIORITIZE?" in html

    def test_public_health_strategic_decision_navigator(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # 5 strategy cards
        assert "card-q1" in html
        assert "card-q2" in html
        assert "card-q3" in html
        assert "card-q4" in html
        assert "card-q5" in html

    def test_public_health_hotspots_and_vulnerabilities(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # Geographic hotspots
        assert "Devanahalli Ward 12" in html
        assert "Mandya Central" in html

        # Demographic vulnerability
        assert "30–44 Years" in html or "30–44" in html
        assert "Silent" in html

    def test_public_health_intervention_efficacy(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # Clinically verified outcomes
        assert "-11.4 mmHg" in html
        assert "-18.6 mg/dL" in html
        assert "617" in html  # Downgrades count

    def test_public_health_overdue_and_asha_outreach_dispatch(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # Overdue cohort tracking
        assert "284" in html
        assert "btnDispatchAshaOutreach" in html
        assert "dispatchAshaOutreach" in html

    def test_public_health_top_five_policy_priorities(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # 5 AI Directives
        assert "btnDirective1" in html
        assert "btnDirective2" in html
        assert "btnDirective3" in html
        assert "btnDirective4" in html
        assert "btnDirective5" in html
        assert "btnApproveAllDirectives" in html
        assert "approveAllDirectives" in html

    def test_public_health_privacy_and_accessibility(self):
        resp = client.get("/public-health-app")
        html = resp.text

        # k >= 10 differential privacy guard
        assert "k-Anonymity Guard" in html or "k &ge; 10" in html
        # Multilingual readiness
        assert "btn-lang-en" in html
        assert "btn-lang-hi" in html
        assert "btn-lang-kn" in html
        # Large touch targets
        assert "min-height: 48px" in html or "min-height:48px" in html

    def test_citizen_role_lock_and_switch_button_removed(self):
        """Verify index.html removes switch role button for citizen and locks citizen to their portal."""
        index_html_path = Path("services/api/static/index.html")
        assert index_html_path.exists()
        html = index_html_path.read_text(encoding="utf-8")

        # Citizen RBAC check in switchTab
        assert "currentUser.role === 'CITIZEN' && tabId !== 'citizen'" in html
        # Switch button completely removed from user profile pill in renderAuthHeader
        assert "Switch Role" not in html
        # openAuthModal blocked for citizen
        assert "currentUser && currentUser.token && currentUser.role === 'CITIZEN'" in html
