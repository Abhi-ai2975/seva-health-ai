# SevaHealth AI

**AI-Native Preventive-Health & NCD Early-Warning Decision-Intelligence Platform**  
*Proposed for the Seva First Innovation Challenge (https://sevainnovationchallenge.in/)*  
*Problem Statement: Non-communicable and emerging lifestyle-oriented diseases*

---

## 1. Product Vision & Mission

SevaHealth AI identifies citizens who are silently progressing toward chronic disease, explains the physiological and lifestyle factors driving their risk, generates personalized preventive lifestyle medicine interventions, continuously monitors their trajectory, and escalates high-risk cases to healthcare professionals.

### The 8-Stage Core Loop
```
SCREEN ──► UNDERSTAND ──► RISK STRATIFY ──► PREDICT ──► INTERVENE ──► MONITOR ──► ESCALATE ──► MEASURE
```

### Three Stakeholder Tiers
1. **Citizen:** Mobile & web companion for guided screening, explainable risk factor breakdown, personalized 30-day preventive habit care plans, and smartwatch sync.
2. **Healthcare Worker & Clinician:** Field screening terminal for ASHA/ANM workers, high-risk triage inbox, AI pre-consultation SOAP notes, and doctor review sign-offs.
3. **Population / Public Health Administrator:** District-level epidemiological heatmaps, NCD prevalence distribution, and intervention outcome efficacy metrics.

---

## 2. Monorepo Structure

```
sevahealth-ai/
  apps/
    citizen-mobile/          # Flutter mobile application architecture & sensor specs
    clinician-web/           # Clinician decision support & triage review portal
    health-worker-web/       # ASHA / ANM field screening terminal
    public-health-web/       # District & state population intelligence dashboard

  services/
    api/                     # FastAPI Gateway & Modular Monolith orchestrator
    identity/                # IAM, OAuth 2.1 / OIDC tokens, RBAC, tenant isolation
    screening/               # CBAC & IDRS survey ingestion and calculators
    risk-engine/             # Multi-domain guideline scoring & SHAP explainability
    intervention-engine/     # 30-day personalized preventive care plan generator
    ai-agent/                # Multi-provider LLM runtime & clinical safety guardrails
    wearable/                # Biometric timeseries normalization (HR, HRV, Sleep, Steps)
    clinical/                # High-risk triage queue & doctor review sign-off
    documents/               # S3 / MinIO medical report & attachment vault
    notifications/           # Triage alerts & preventive reminder dispatcher
    population-intelligence/ # Regional epidemiological analytics & outcome delta engine

  packages/
    ui/                      # Shared design tokens and web interface components
    types/                   # Core enumerations (UserRole, RiskTier, TrajectoryTrend)
    config/                  # Pydantic v2 application settings and validator
    validation/              # Clinical bounds validation & prompt injection defenses
    clinical-models/         # LOINC observations, FHIR R4 resources, openEHR mappings
    ai-schemas/              # Typed JSON schemas for LLM reasoning & care plans
    auth/                    # JWT token creation, decoding, and role guards
    observability/           # Non-blocking async event buffer and audit logger

  infrastructure/
    docker/                  # Production & dev Dockerfiles
    postgres/                # PostgreSQL init scripts & JSONB indexes
    ehrbase/                 # openEHR REST & archetype specifications
    redis/                   # Redis session and rate limiter configurations
    object-storage/          # MinIO S3 bucket configuration
    monitoring/              # OpenTelemetry collector & metrics definitions

  ml/                        # Datasets, feature engineering, and inference pipelines
  agents/                    # Specialized preventive, clinical, and screening agents
  docs/                      # Complete system specifications & reuse analyses
  scripts/                   # Seeding scripts and end-to-end demo verification
  tests/                     # Comprehensive automated unit & integration test suites
```

---

## 3. Quickstart & Local Execution

### Option A: Direct Local Run (Zero External Dependencies)
SevaHealth AI includes built-in dual-mode storage (embedded SQLite fallback) and deterministic offline AI providers, so you can run and evaluate the complete platform locally without Docker or external cloud keys:

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Seed realistic synthetic Indian personas & regional screening data
python scripts/seed_data.py

# 3. Start the FastAPI Gateway
python -m uvicorn services.api.main:app --reload --port 8000
```
Open your browser to:
* **Interactive Web Portal:** [http://localhost:8000/](http://localhost:8000/)
* **Interactive Swagger API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **System Health Probe:** [http://localhost:8000/health](http://localhost:8000/health)

### Option B: Containerized Run (Docker Compose)
```bash
docker compose up --build -d
```

---

## 4. Automated Testing & Verification

Run the complete test suite:
```bash
pytest tests -v
```

Run the end-to-end 8-stage core loop demo verification:
```bash
python scripts/test_e2e.py
```

---

## 5. End-to-End Platform Verification Flow

When opening [http://localhost:8000/](http://localhost:8000/), an evaluator can verify the platform end-to-end in 5 minutes:

1. **Step 1: Citizen Health Companion** &rarr; Inspect citizen health indicators and biomarkers (HbA1c 6.2%, FBG 118 mg/dL, BP 138/88, BMI 27.2) and active preventive indicators.
2. **Step 2: Preventive Screening** &rarr; Inspect integrated CBAC and IDRS risk surveys via the Take Self-Check modal or Field Worker Intake.
3. **Step 3: Multi-Factor Risk Assessment** &rarr; View composite risk (*HIGH: 61%*), prediabetes probability (*65%*), and prehypertension risk (*60%*).
4. **Step 4: Explainability Waterfall** &rarr; Understand top drivers: *+22% Impaired Glycemia (HbA1c 6.2%)*, *+18% Vascular Pressure (138/88 mmHg)*, and protective factors (*-15% Tobacco Abstinence*). Notice the clear safety disclaimer: *"Clinical review recommended"*.
5. **Step 5: 30-Day Preventive Care Plan** &rarr; Review tailored nutrition guidance (swapping white rice with foxtail millet/ragi) and click to check off daily habit tasks, watching adherence update live.
6. **Step 6: Wearable Biometrics Sync** &rarr; Click "Sync Wearables" to ingest 14 days of normalized smartwatch telemetry (Resting HR, HRV, sleep stages, daily steps).
7. **Step 7: Clinician Triage Review** &rarr; Switch to the Clinician tab. Review *Lakshmi Devi* (escalated for Stage 2 HTN 164/98 mmHg), inspect the AI-drafted pre-consult SOAP note, and click **"Doctor Sign-off & Approve Care Plan"**.
8. **Step 8: Population Intelligence** &rarr; Switch to the Population Health tab. View district heatmaps and verify the **Outcome Efficacy Delta**: Citizens with &ge;75% adherence achieved an average **-6.4 mmHg systolic BP drop** and **-14.2 mg/dL fasting glucose reduction** over 30 days!

### Environment Baseline & Repeatability (TC-DEP-005)

To restore clean initial database baseline and verify repeatability with 100% determinism:

* **Interactive Web Portal**: Navigate to **Deployment Verification** &rarr; Click **"5. Baseline Reset"** &rarr; Click **"Reset Environment Baseline"** (triggers `/api/v1/deployment/demo-reset-audit`).
* **Command Line Interface**:
  ```bash
  # Option 1: Seed Data Baseline Reset
  python scripts/seed_data.py --reset

  # Option 2: Automated End-to-End Verification
  python scripts/test_e2e.py
  ```

---

## 6. Clinical Safety & Governance

SevaHealth AI is strictly non-diagnostic:
* **AI may:** Stratify preventive risk, explain physiological drivers, formulate lifestyle medicine habits, summarize pre-consult history, and flag critical thresholds.
* **AI must NOT:** Autonomously diagnose disease, prescribe pharmaceutical medications, titrate dosages, or issue medical orders.
* **Human-in-the-Loop:** All escalated cases require licensed physician sign-off. Probabilistic risk is never presented as a confirmed medical condition.

---

## 7. Open-Source Reuse & Attribution Matrix

In compliance with open-source licensing, all eleven upstream reference repositories are documented in [`docs/REUSE_MATRIX.md`](docs/REUSE_MATRIX.md) and [`docs/repository-reuse-analysis.md`](docs/repository-reuse-analysis.md). No copyright notices have been removed:

* `bezs-iam` (MIT, Yazhnimalan) &rarr; Adapted for RBAC, JWT tokens, and audit schemas.
* `bezs-filenest` (MIT, Yazhnimalan) &rarr; Adapted for S3/MinIO presigned medical report vault.
* `bezs-pipeline` (MIT, alphaesAI) &rarr; Adapted for tabular screening validation pipelines.
* `refactoragent` (MIT-compatible) &rarr; Adapted for clinical prompt structuring, safety barriers, and multi-provider LLM abstractions.
* `bezs-hms` (MIT, Yazhnimalan) &rarr; Adapted for clinician triage queue and consultation review patterns.
* `DrGodly-Mobile-Aplication` (Saythu000) &rarr; Referenced for mobile intake UX and BLE/Health Connect sensor matrix.
* `ehrbase` (Apache-2.0, openEHR Foundation) &rarr; Referenced for clinical archetype rigor.
* `bezs-emr-gql` (MIT, Yazhnimalan) &rarr; Reused for LOINC terminology and FHIR R4 Pydantic schemas.
* `bezs-emr-mcp` (MIT, Yazhnimalan) &rarr; Adapted for AI agent clinical tool contracts.
* `bezs-observability` (MIT, Yazhnimalan) &rarr; Adapted for async event buffer and telemetry middleware.
* `open-wearables` (MIT, Momentum) &rarr; Reused for normalized biometric timeseries schemas and recovery algorithms.





# SEVAHEALTH AI — COMPLETE MASTER CHEAT SHEET

## 1. PROJECT INTRODUCTION

**Project Name:** SevaHealth AI

**Project Type:** AI-Native Preventive Healthcare & NCD Early-Warning Decision-Intelligence Platform

**Main Objective:**
Diabetes, Hypertension, Cardiovascular Diseases aur Chronic Kidney Disease ke potential risks ko early stage mein identify karne aur preventive care support karne ke liye platform develop karna.

**Main Features:**
- Health screening and risk assessment
- Explainable risk factors
- Personalized 30-day preventive care plans
- Daily habit tracking
- Wearable health-data integration
- High-risk alerts and referrals
- AI-assisted clinical documentation
- Public health analytics and dashboards

**One-line Explanation:**
SevaHealth AI uses healthcare data and AI-assisted workflows to support early risk identification, preventive care planning, and healthcare monitoring.

---

## 2. TECHNOLOGY STACK

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| FastAPI | REST API development |
| PostgreSQL 16 | Relational database |
| JSONB | Flexible structured/semi-structured data |
| Gemini, Claude, GPT | Multi-provider AI runtime |
| Docker | Containerized execution |
| Swagger/OpenAPI | API documentation and testing |
| PowerShell | Windows automation |
| HL7 FHIR R4 | Healthcare data interoperability standard |
| openEHR | Electronic health record modelling approach |
| ABDM | Ayushman Bharat Digital Mission context |
| DISHA | Digital Information Security in Healthcare Act context |

**Remember:** Python → FastAPI → PostgreSQL → AI services → Web dashboards.

Note: Listing a technology or standard does not by itself prove that every feature or compliance requirement has been fully implemented.

---

## 3. ALL PROJECT URLS

Backend port: 8000

1. Unified Portal
   http://localhost:8000/

2. Clinician App
   http://localhost:8000/clinician-app

3. Health Worker App
   http://localhost:8000/health-worker-app

4. Citizen App
   http://localhost:8000/citizen-app

5. Public Health Dashboard
   http://localhost:8000/public-health-app

6. Observability Dashboard
   http://localhost:8000/observability

7. Swagger API Documentation
   http://localhost:8000/docs

**Important:** Backend server running hona chahiye. Localhost URLs sirf tumhare computer par accessible hain.

HTTP 200 OK = Request successfully respond hui. Isse saare application features correct hone ki guarantee nahi milti.

---

## 4. THREE MAIN USER ROLES

### A. Citizen
- Health profile and screening
- Risk assessment results
- Preventive care plan
- Daily habit check-ins
- Wearable data integration

### B. Health Worker / Clinician
- CBAC/IDRS screening
- High-risk patient triage
- Patient health information
- AI-assisted SOAP notes
- Doctor review and sign-off
- Referral management

### C. Public Health Administrator
- District-level health analytics
- NCD distribution
- Epidemiological heatmaps
- Cohort outcome measurements

**Memory Trick:** Citizen → Care Team → Community Dashboard.

---

## 5. EIGHT-STAGE HEALTHCARE WORKFLOW

1. SCREEN — Screening data collect karna.
2. UNDERSTAND — Patient history, lifestyle and measurements understand karna.
3. RISK STRATIFY — Risk score aur category determine karna.
4. PREDICT — Available data se potential risks assess karna.
5. INTERVENE — Preventive care plan generate/support karna.
6. MONITOR — Check-ins aur health trends track karna.
7. ESCALATE — Appropriate alerts aur referrals support karna.
8. MEASURE — Progress aur outcomes evaluate karna.

**Memory Trick:**
Screen → Understand → Score → Predict → Prevent → Monitor → Escalate → Measure.

Risk assessment ko confirmed medical diagnosis nahi samajhna chahiye.

---

## 6. DATABASE TABLES — QUICK REVISION

### Group 1: Demographics and Hierarchy
- organizations
- care_teams
- care_team_members
- patients
- profiles
- family_histories
- lifestyle_profiles

### Group 2: Clinical Measurements
- vital_signs
- lab_results
- wearable_connections
- wearable_observations
- medications
- medication_adherences

### Group 3: Screening and Risk Assessment
- screenings
- screening_results
- risk_assessments
- risk_factors
- risk_factor_contributions
- risk_trajectories

### Group 4: Interventions and Care Plans
- intervention_plans
- interventions
- goals
- check_ins
- care_plans

### Group 5: Clinical Encounters and Triage
- clinical_encounters
- referrals
- alerts
- notifications

### Group 6: Security, Audit and Documents
- consents
- documents
- document_references
- audit_events

**Important relationships:**

Patients → Screenings → Screening Results

Patients → Risk Assessments → Risk Factor Contributions

Patients → Intervention Plans → Interventions → Check-ins

Patients → Clinical Encounters / Referrals / Alerts

Documents → Document References

**Table-count note:** Project documentation mentions 31 production relational tables, while a command comment mentions 32 domain tables. Verify the actual schema before quoting the final count in an interview.

---

## 7. BACKEND ARCHITECTURE

Request flow:

User Interface
    ↓
FastAPI Endpoint
    ↓
Input Validation
    ↓
Business Logic / Risk Assessment / AI Service
    ↓
PostgreSQL Database
    ↓
JSON Response
    ↓
Frontend Dashboard

**FastAPI responsibilities:**
- HTTP endpoints
- Request and response handling
- Input validation
- Application service integration
- API documentation

**PostgreSQL responsibilities:**
- Patient and profile records
- Screening and assessment history
- Clinical measurements
- Intervention plans
- Alerts and audit records

**AI responsibilities:**
- AI-assisted workflows
- Clinical note drafting
- Support for personalized care content
- Explainability or summarization, where implemented

Exact implementation should be verified in the source code.

---

## 8. PROJECT EXECUTION COMMANDS

Open PowerShell in the project root folder.

### Start development server

    .\run.ps1 dev

Alternative:

    python -m uvicorn services.api.main:app --reload --port 8000 --host 0.0.0.0

### View database tables

    python show_db_tables.py

### Inspect a table

    python show_db_tables.py --table screenings

Other documented examples:

    python show_db_tables.py --table citizens
    python show_db_tables.py --table users
    python show_db_tables.py --table triage

Check the actual supported table names if a command returns an error.

### Inspect schema

    python show_db_tables.py --table schema

### Seed demo data

    python scripts/seed_data.py

Or:

    .\run.ps1 seed

### Run tests

    .\run.ps1 test

Alternative:

    python -m pytest tests -v --tb=short

### Run end-to-end tests

    .\run.ps1 e2e

Alternative:

    python scripts/test_e2e.py

### Start Docker environment

    docker compose up --build -d

### Stop a process using port 8000

    Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess -Force

Use the port-kill command only if port 8000 is actually occupied and you have confirmed the process can be stopped.

---

## 9. IMPORTANT PROJECT SCRIPTS

| File | Purpose |
|---|---|
| show_db_tables.py | Database table explorer |
| scripts/seed_data.py | Generate synthetic demo data |
| scripts/test_e2e.py | End-to-end workflow verification |
| scripts/migrate.py | Apply database schema migrations |
| scripts/backup_database.py | Database snapshot backups |
| scripts/benchmark_scale.py | Simulate larger workloads |
| run.ps1 | Master PowerShell runner |
| infrastructure/postgres/migrations/001_initial_domain_model.sql | Initial relational domain schema |

Demo-data and benchmark scripts are for testing and development unless separately configured and verified for another environment.

---

## 10. TESTING CHECKLIST

Before demonstrating the project:

[ ] Backend starts without errors.

[ ] Unified Portal opens.

[ ] Clinician App opens.

[ ] Health Worker App opens.

[ ] Citizen App opens.

[ ] Public Health Dashboard opens.

[ ] Observability dashboard opens.

[ ] Swagger documentation opens.

[ ] Database connection works.

[ ] Screening submission is tested.

[ ] Risk assessment output is checked.

[ ] Preventive plan generation is checked.

[ ] Check-in and monitoring flow is checked.

[ ] Alerts and referral workflows are checked.

[ ] Automated tests pass.

[ ] End-to-end test completes successfully.

[ ] Demo data is present where required.

[ ] No real patient information is exposed during the demo.

---

## 11. INTERVIEW QUESTIONS — SHORT ANSWERS

Q1. What is SevaHealth AI?

It is an AI-assisted preventive healthcare platform designed to support early NCD risk identification and preventive care planning.

Q2. Why did you use FastAPI?

FastAPI provides Python-based API development, request validation and automatic interactive API documentation.

Q3. Why PostgreSQL?

PostgreSQL supports relational data, constraints, transactions and JSONB for flexible data structures.

Q4. What are NCDs?

NCDs are non-communicable diseases such as diabetes, hypertension and cardiovascular diseases.

Q5. What is risk stratification?

It means classifying people into risk categories to help prioritize appropriate follow-up.

Q6. What is explainable AI?

It helps people understand the factors contributing to an AI model's output instead of presenting only a result.

Q7. What is a SOAP note?

SOAP stands for Subjective, Objective, Assessment and Plan. It is a structured format for clinical documentation.

Q8. What is CBAC/IDRS?

They are screening approaches used in India for community health risk assessment and diabetes risk screening.

Q9. What is JSONB?

JSONB is a PostgreSQL data type for storing and querying JSON data in a binary representation.

Q10. What is a database migration?

It is a controlled change to a database schema, such as creating a table or adding a column.

Q11. What is end-to-end testing?

It checks whether multiple connected parts of an application work together through a complete workflow.

Q12. What is Docker?

Docker packages an application and its dependencies into containers for consistent execution across environments.

Q13. How does the application help healthcare workers?

It supports screening, patient-risk review, triage, clinical note drafting and referral workflows.

Q14. Is AI making the final medical decision?

The intended role is decision support. Clinical assessment and medical decisions require qualified healthcare professionals.

Q15. How do you start the project?

I open PowerShell in the project directory and run .\run.ps1 dev, then access the local portal and Swagger documentation.

---

## 12. 30-SECOND PROJECT PRESENTATION

My project is SevaHealth AI, an AI-assisted preventive healthcare platform.

It focuses on early risk identification for non-communicable diseases such as diabetes and hypertension.

The system supports health screening, risk assessment, explainable results, personalized preventive care plans and monitoring.

It provides separate interfaces for citizens, healthcare workers, clinicians and public health administrators.

The backend uses Python and FastAPI, with PostgreSQL for data storage. The project also includes API documentation, testing scripts and Docker-based execution support.

---

## 13. FINAL REVISION — REMEMBER THESE 10 POINTS

1. Project: SevaHealth AI.
2. Goal: Early NCD risk identification and preventive care.
3. Backend: Python + FastAPI.
4. Database: PostgreSQL 16 + JSONB.
5. AI: Multi-provider runtime with offline fallback.
6. Roles: Citizen, Clinician/Health Worker, Administrator.
7. Workflow: Screen → Assess → Intervene → Monitor → Escalate → Measure.
8. API docs: http://localhost:8000/docs
9. Start command: .\run.ps1 dev
10. Tests: pytest + end-to-end verification.

**Golden Rule:** Interview mein wahi features apne implemented work ke roop mein claim karna jinka code, test ya working demonstration tum verify kar sakti ho.