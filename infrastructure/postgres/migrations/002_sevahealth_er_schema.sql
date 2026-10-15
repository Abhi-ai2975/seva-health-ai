-- ==============================================================================
-- SevaHealth AI: Unified Relational Schema & Entity-Relationship Architecture
-- Migration: 002_sevahealth_er_schema.sql
-- Conforms to: HL7 FHIR R4, openEHR Archetypes, ABDM (Ayushman Bharat Digital Mission)
-- Diagram: SEVAHEALTH AI Production ER Architecture
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

SET timezone = 'UTC';

-- ==============================================================================
-- 1. ORGANIZATIONS (Administrative Health Mission & Jurisdiction Hierarchy)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL DEFAULT 'STATE_HEALTH_MISSION', -- STATE_HEALTH_MISSION | DISTRICT_HEALTH_OFFICE | PHC | CHC
    jurisdiction VARCHAR(255) NOT NULL DEFAULT 'Karnataka',   -- e.g. "Karnataka", "Mysuru District", "Ward 12"
    parent_organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ==============================================================================
-- 2. CARE TEAMS (Multi-Disciplinary Field & Clinical Teams)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS care_teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,                               -- e.g. "PHC Bilikere Community Team", "Ward 12 ASHA Cell"
    jurisdiction VARCHAR(255) NOT NULL,
    lead_clinician_id VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_care_teams_organization FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 3. PATIENTS (Central Core Patient / Citizen Entity)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    abha_id VARCHAR(64) UNIQUE,                               -- ABDM 14-digit Health ID (e.g. 91-4829-1029-4820)
    user_id VARCHAR(128) UNIQUE,                              -- Linked Auth User Account (RBAC)
    primary_care_team_id UUID REFERENCES care_teams(id) ON DELETE SET NULL,
    clinical_type VARCHAR(64) NOT NULL DEFAULT 'CITIZEN_PATIENT',
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Registration source, telemetry, digital consent
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_patients_organization FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
    CONSTRAINT fk_patients_care_team FOREIGN KEY (primary_care_team_id) REFERENCES care_teams(id) ON DELETE SET NULL
);

-- ==============================================================================
-- 4. PROFILES (Demographics, Address & Geographic Allocation)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    abha_id VARCHAR(64),
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128) NOT NULL,
    birth_date DATE NOT NULL,
    gender VARCHAR(16) NOT NULL DEFAULT 'OTHER',              -- MALE | FEMALE | OTHER
    phone VARCHAR(32) NOT NULL,
    email VARCHAR(128),
    state VARCHAR(64) NOT NULL DEFAULT 'Karnataka',
    district VARCHAR(64) NOT NULL DEFAULT 'Mysuru',
    sub_district VARCHAR(64) NOT NULL DEFAULT 'Mysuru Urban',
    village_or_ward VARCHAR(128) NOT NULL DEFAULT 'Ward 1',
    primary_language VARCHAR(16) NOT NULL DEFAULT 'en',
    clinical_type VARCHAR(64) NOT NULL DEFAULT 'DEMOGRAPHIC_PROFILE',
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_profiles_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 5. VITAL SIGNS (Biometric Observations: BP, HR, BMI, Waist)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS vital_signs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id VARCHAR(64),
    clinical_type VARCHAR(64) NOT NULL,                       -- SYSTOLIC_BP | DIASTOLIC_BP | HEART_RATE | BMI | WAIST_CIRCUMFERENCE
    value NUMERIC(10,3) NOT NULL,
    unit VARCHAR(32) NOT NULL,                                -- mmHg, bpm, kg/m2, cm
    measurement_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_flagged_abnormal BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(64) NOT NULL DEFAULT 'COMMUNITY_SCREENING', -- ASHA_HOME_SCREENING | CLINIC_OPD | SMARTWATCH_WEARABLE
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Device info, operator ID, calibration hash
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_vital_signs_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 6. LAB RESULTS (Point-of-Care & Clinical Lab Biomarkers)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS lab_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id VARCHAR(64),
    clinical_type VARCHAR(64) NOT NULL,                       -- FASTING_GLUCOSE | HBA1C | TOTAL_CHOLESTEROL | TRIGLYCERIDES | SERUM_CREATININE
    test_name VARCHAR(128) NOT NULL,
    loinc_code VARCHAR(32),
    value NUMERIC(10,3) NOT NULL,
    unit VARCHAR(32) NOT NULL,                                -- mg/dL, %, mg/L
    reference_range VARCHAR(64) DEFAULT 'NORMAL',
    interpretation VARCHAR(32) NOT NULL DEFAULT 'NORMAL',     -- NORMAL | BORDERLINE | ELEVATED | CRITICAL
    measurement_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Laboratory info, assay protocol, LOINC code
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_lab_results_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 7. SCREENINGS (CBAC, IDRS, and Population Health Surveys)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS screenings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    program_id VARCHAR(64) NOT NULL DEFAULT 'NCD_PREVENTION_2026',
    screening_type VARCHAR(64) NOT NULL DEFAULT 'NCD_COMPREHENSIVE_CBAC_IDRS', -- CBAC | IDRS | COMPREHENSIVE
    administered_by VARCHAR(128) NOT NULL,
    risk_json JSONB NOT NULL DEFAULT '{}'::jsonb,             -- Full survey responses, answers, scores, vitals payload
    screened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_screenings_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 8. RISK & INTERVENTIONS (NCD Stratification, Explainability & Care Plans)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS risk_interventions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    screening_id UUID REFERENCES screenings(id) ON DELETE SET NULL,
    abha_id VARCHAR(64),
    clinical_type VARCHAR(64) NOT NULL DEFAULT 'NCD_COMPOSITE_RISK',
    overall_score NUMERIC(5,4) NOT NULL CHECK (overall_score >= 0.0000 AND overall_score <= 1.0000),
    overall_tier VARCHAR(32) NOT NULL,                        -- LOW | MODERATE | HIGH | CRITICAL
    trajectory VARCHAR(32) NOT NULL DEFAULT 'STABLE',         -- IMPROVING | STABLE | DETERIORATING
    domain_scores JSONB NOT NULL DEFAULT '{}'::jsonb,         -- {diabetes, hypertension, cvd, metabolic, ckd}
    intervention_plan JSONB NOT NULL DEFAULT '{}'::jsonb,     -- 4 Pillars: Nutrition, Physical Activity, Sleep, Stress
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Explainable AI waterfall, model version (v2.4.0)
    assessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_risk_intervention_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    CONSTRAINT fk_risk_intervention_screening FOREIGN KEY (screening_id) REFERENCES screenings(id) ON DELETE SET NULL
);

-- ==============================================================================
-- 9. WEARABLES (Continuous Physiological Telemetry & IoT Sync)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS wearables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id VARCHAR(64),
    device_id VARCHAR(128) NOT NULL DEFAULT 'BLE_FITNESS_TRACKER',
    clinical_type VARCHAR(64) NOT NULL,                       -- RESTING_HEART_RATE | HRV_RMSSD | DAILY_STEPS | SLEEP_STAGE | SPO2
    value NUMERIC(10,3) NOT NULL,
    unit VARCHAR(32) NOT NULL,                                -- bpm, ms, steps, minutes, %
    measurement_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Sensor type (PPG, 3-Axis Accel), sync latency, battery
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_wearables_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
);

-- ==============================================================================
-- 10. AUDIT EVENTS (Forensic Security, Access Logs & Provenance)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    patient_id UUID REFERENCES patients(id) ON DELETE SET NULL,
    actor_id VARCHAR(128) NOT NULL,
    actor_role VARCHAR(64) NOT NULL,                          -- CITIZEN | HEALTH_WORKER | CLINICIAN | PUBLIC_HEALTH_ADMIN
    action VARCHAR(64) NOT NULL,                              -- USER_REGISTERED | LOGIN_SUCCESS | CONSENT_GRANTED | VITALS_RECORDED
    clinical_type VARCHAR(64) NOT NULL DEFAULT 'SECURITY_AUDIT',
    resource_type VARCHAR(64) NOT NULL,                       -- Patient | VitalSigns | Screening | RiskAssessment
    resource_id VARCHAR(128) NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,            -- IP address, user-agent, correlation_id, trace_id
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_audit_events_organization FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL,
    CONSTRAINT fk_audit_events_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE SET NULL
);

-- ==============================================================================
-- PERFORMANCE INDEXES (Explicitly Annotated in ER Diagram)
-- ==============================================================================

-- 1. Profiles Geographic Index (Featured in ER Callout Circle)
CREATE INDEX IF NOT EXISTS idx_profiles_geography 
    ON profiles(state, district, sub_district, village_or_ward);

-- 2. Vital Signs Patient & Time Composite Index
CREATE INDEX IF NOT EXISTS idx_vital_signs_patient_id 
    ON vital_signs(patient_id);

CREATE INDEX IF NOT EXISTS idx_vital_signs_patient_time 
    ON vital_signs(patient_id, measurement_timestamp DESC);

-- 3. Lab Results Patient & Time Composite Index
CREATE INDEX IF NOT EXISTS idx_lab_results_patient_time 
    ON lab_results(patient_id, measurement_timestamp DESC);

-- 4. Screenings Patient & Time Index
CREATE INDEX IF NOT EXISTS idx_screenings_patient_time 
    ON screenings(patient_id, screened_at DESC);

-- 5. Risk & Intervention Patient Index
CREATE INDEX IF NOT EXISTS idx_risk_intervention_patient 
    ON risk_interventions(patient_id, assessed_at DESC);

-- 6. Wearables Time-Series Composite Index
CREATE INDEX IF NOT EXISTS idx_wearables_patient_time 
    ON wearables(patient_id, measurement_timestamp DESC);

-- 7. Audit Events Forensic Index
CREATE INDEX IF NOT EXISTS idx_audit_events_patient 
    ON audit_events(patient_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_created 
    ON audit_events(created_at DESC);
