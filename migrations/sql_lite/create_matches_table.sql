CREATE TABLE matches (
    -- Keys
    job_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    -- Core matching constraints
    is_match BOOLEAN NOT NULL, -- false if REJECTED / true if any other
    suitability_tier TEXT NOT NULL, -- SUITABLE / STRECH / RUNWAY / REJECTED
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING / APPLIED / GHOSTED / INTERVIEWING / REJECTED (Early) / REJECTED (Late) / Declined / OFFER
    technical_capability_score REAL NOT NULL, -- score [0, 1] whether candidate suitable by technologies and experience
    strategic_value_score REAL NOT NULL, -- score [0, 1] whether the company has complex challenges and seems perspective for user's growth
    strategic_reason TEXT NOT NULL, -- single sentence explaining why it matches or fails based on constraints

    -- Metadata (in case raw text was sent)
    extracted_company TEXT NOT NULL,
    extracted_title TEXT NOT NULL,
    extracted_location_status TEXT NOT NULL,

    -- Analytics
    analytics TEXT NOT NULL, -- json containing arrays of pros, cons, warnings

    -- AI Suggestions
    cv_modification_points TEXT NOT NULL, -- array of strings explaining what to improve in the CV
    tailored_cover_letter TEXT NOT NULL, -- AI generated cover letter based on sentences extracted from profile.md
    application_form_answers TEXT NOT NUll, -- array of question-answer objects derived from application form. AI generated answers based on profile.md

    -- Debug
    internal_analysis_cot TEXT NOT NULL, -- json containing Chain-of-thought analysis of AI model
    internal_scoring_breakdown TEXT NOT NULL, -- json containing phases deductions
    
    -- Technical
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (job_id, user_id),
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);