export type ApplicationStatus =
  | 'DISCOVERED' | 'ANALYZED' | 'SKIPPED' | 'READY_TO_APPLY' | 'FORM_LOADING'
  | 'FORM_ANALYZED' | 'NEEDS_USER_INPUT' | 'READY_TO_ADVANCE' | 'READY_FOR_REVIEW' | 'APPROVED'
  | 'SUBMITTING' | 'SUBMISSION_UNCONFIRMED' | 'SUBMITTED' | 'FAILED' | 'WAITING_FOR_USER' | 'REJECTED'
  | 'INTERVIEW' | 'OFFER' | 'WITHDRAWN'
  | 'ARCHIVED'

export interface DashboardStats {
  jobs_discovered: number
  strong_matches: number
  ready_to_apply: number
  applications_submitted: number
  interviews: number
  offers: number
  highest_matches: Array<{ job_id: string; company: string; role: string; score: number }>
  application_queue: Array<{ application_id: string; company: string; role: string; status: string; date: string }>
  recent_applications: Array<{ application_id: string; company: string; role: string; status: string; date: string }>
}

export interface Education {
  id?: string
  institution: string
  degree?: string
  field_of_study?: string
  start_date?: string
  graduation_date?: string
  current_student: boolean
}

export interface WorkExperience {
  id?: string
  title: string
  company?: string
  start_date?: string
  end_date?: string
  description?: string
}

export interface Language { id?: string; language: string; proficiency?: string }

export interface CandidateProfile {
  id?: string
  first_name?: string; last_name?: string; preferred_name?: string; email?: string; phone?: string
  city?: string; country?: string; postal_code?: string; street_name?: string; house_number?: string
  address?: string; nationality?: string
  date_of_birth?: string; gender?: string; honorific_title?: string; name_suffix?: string
  current_title?: string; linkedin?: string; github?: string; portfolio?: string; personal_website?: string
  skills: string[]; programming_languages: string[]; frameworks: string[]; tools: string[]
  preferred_job_types: string[]; preferred_locations: string[]; remote_preference?: string
  minimum_match_score: number; preferred_technologies: string[]; undesired_roles: string[]; seniority: string[]
  availability?: string; preferred_start_date?: string; work_authorization?: string; visa_status?: string
  notice_period?: string; salary_expectation?: string; weekly_hours?: string
  relocation_willingness?: string; drivers_license?: string
  travel_willingness?: string; consider_other_positions?: string
  educations: Education[]; work_experiences: WorkExperience[]; languages: Language[]
  revision?: number; created_at?: string; updated_at?: string
}

export interface Resume {
  id: string; filename: string; label: string; target_roles: string[]; skills: string[]
  is_default: boolean; created_at: string
}

export interface Job {
  id: string; company: string; title: string; location?: string; description: string
  employment_type?: string; source_url?: string; application_url?: string; source: string
  external_job_id?: string; probable_duplicate_of?: string; created_at: string; updated_at: string
  match_score?: number; recommendation?: string
}

export interface JobAnalysis {
  overall_score: number; skill_match_score: number; experience_match_score: number
  education_match_score: number; location_match_score: number; recommendation: string
  matching_skills: string[]; missing_skills: string[]; must_have_requirements: string[]
  unmet_must_have_requirements: string[]; strengths: string[]; concerns: string[]
  recommended_resume_id?: string; summary: string; analysis_id: string; ai_used: boolean; created_at: string
}

export interface ApplicationField {
  id: string; step_index: number; active: boolean; field_key?: string; selector: string; question: string; field_type: string
  translated_question?: string; source_language?: string
  answer?: string; source: string; confidence: number; required: boolean; uncertain: boolean
  requires_verification: boolean; disqualifying: boolean; character_limit?: number; options: string[]
  translated_options: string[]; translation_confidence: number; skipped: boolean
  resolution_message?: string; ai_retryable: boolean
}

export interface Application {
  id: string; job_id: string; company: string; role: string; location?: string; source_url?: string
  match_score?: number; resume_id?: string; resume_label?: string; resume_filename?: string; status: ApplicationStatus
  current_step: number; current_url?: string; waiting_reason?: string; error_message?: string; created_at: string; approved_at?: string
  submitted_at?: string; archived_at?: string; is_demo: boolean; can_delete: boolean; delete_blocked_reason?: string
  can_restart: boolean; restart_blocked_reason?: string
  fields: ApplicationField[]
}

export interface AnswerBankEntry {
  id: string; canonical_key: string; question: string; answer: string; aliases: string[]
  created_at: string; updated_at: string
}

export interface AIConnectionStatus {
  selected_provider: 'codex' | 'openai' | 'auto' | 'none'
  active_provider: 'codex' | 'openai' | null
  ai_configured: boolean
  model: string | null
  codex_connected: boolean
  codex_account_type: string | null
  codex_email: string | null
  codex_plan_type: string | null
  codex_runtime_available: boolean
  codex_error: string | null
  openai_api_configured: boolean
  ai_invocation_count: number
  auto_submit: boolean
  browser_headless: boolean
  data_directory: string
}

export interface CodexLoginStart {
  login_id: string
  auth_url: string
  status: 'pending'
}

export interface CodexLoginStatus {
  login_id: string
  status: 'pending' | 'completed' | 'failed' | 'cancelled'
  error: string | null
}
