-- ==============================================================================
-- SevaHealth AI: SQLite Production Schema with Full Foreign Key Relationships
-- Matches: SEVAHEALTH AI Production ER Diagram Architecture
-- File: scripts/create_er_tables_sqlite.sql
-- ==============================================================================

PRAGMA foreign_keys = ON;

-- 1. Organizations
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'STATE_HEALTH_MISSION',
    jurisdiction TEXT NOT NULL DEFAULT 'Karnataka',
    parent_organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 2. Care Teams
CREATE TABLE IF NOT EXISTS care_teams (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    lead_clinician_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 3. Patients
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    abha_id TEXT UNIQUE,
    user_id TEXT UNIQUE,
    primary_care_team_id TEXT REFERENCES care_teams(id) ON DELETE SET NULL,
    clinical_type TEXT NOT NULL DEFAULT 'CITIZEN_PATIENT',
    provenance TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 4. Profiles
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    abha_id TEXT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    gender TEXT NOT NULL DEFAULT 'OTHER',
    phone TEXT NOT NULL,
    email TEXT,
    state TEXT NOT NULL DEFAULT 'Karnataka',
    district TEXT NOT NULL DEFAULT 'Mysuru',
    sub_district TEXT NOT NULL DEFAULT 'Mysuru Urban',
    village_or_ward TEXT NOT NULL DEFAULT 'Ward 1',
    primary_language TEXT NOT NULL DEFAULT 'en',
    clinical_type TEXT NOT NULL DEFAULT 'DEMOGRAPHIC_PROFILE',
    provenance TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 5. Vital Signs
CREATE TABLE IF NOT EXISTS vital_signs (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id TEXT,
    clinical_type TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    measurement_timestamp TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    is_flagged_abnormal INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'COMMUNITY_SCREENING',
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 6. Lab Results
CREATE TABLE IF NOT EXISTS lab_results (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id TEXT,
    clinical_type TEXT NOT NULL,
    test_name TEXT NOT NULL,
    loinc_code TEXT,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    reference_range TEXT DEFAULT 'NORMAL',
    interpretation TEXT NOT NULL DEFAULT 'NORMAL',
    measurement_timestamp TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 7. Screenings
CREATE TABLE IF NOT EXISTS screenings (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    program_id TEXT NOT NULL DEFAULT 'NCD_PREVENTION_2026',
    screening_type TEXT NOT NULL DEFAULT 'NCD_COMPREHENSIVE_CBAC_IDRS',
    administered_by TEXT NOT NULL,
    risk_json TEXT NOT NULL DEFAULT '{}',
    screened_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 8. Risk & Interventions
CREATE TABLE IF NOT EXISTS risk_interventions (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    screening_id TEXT REFERENCES screenings(id) ON DELETE SET NULL,
    abha_id TEXT,
    clinical_type TEXT NOT NULL DEFAULT 'NCD_COMPOSITE_RISK',
    overall_score REAL NOT NULL,
    overall_tier TEXT NOT NULL,
    trajectory TEXT NOT NULL DEFAULT 'STABLE',
    domain_scores TEXT NOT NULL DEFAULT '{}',
    intervention_plan TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '{}',
    assessed_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 9. Wearables
CREATE TABLE IF NOT EXISTS wearables (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    abha_id TEXT,
    device_id TEXT NOT NULL DEFAULT 'BLE_FITNESS_TRACKER',
    clinical_type TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    measurement_timestamp TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 10. Audit Events
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE SET NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    clinical_type TEXT NOT NULL DEFAULT 'SECURITY_AUDIT',
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_profiles_geography ON profiles(state, district, sub_district, village_or_ward);
CREATE INDEX IF NOT EXISTS idx_vital_signs_patient_id ON vital_signs(patient_id);
CREATE INDEX IF NOT EXISTS idx_vital_signs_patient_time ON vital_signs(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_lab_results_patient_time ON lab_results(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_screenings_patient_time ON screenings(patient_id, screened_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_intervention_patient ON risk_interventions(patient_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_wearables_patient_time ON wearables(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_patient ON audit_events(patient_id, created_at DESC);
