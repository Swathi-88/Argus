// Response shapes mirroring app/schemas.py. Kept hand-written rather than
// generated so the console documents the contract it actually depends on.

export type Role = 'ANALYST' | 'MANAGER' | 'AUDITOR'
export type Tier = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type AlertStatus = 'NEW' | 'CONFIRMED' | 'DISMISSED' | 'ESCALATED' | 'INFO_REQUESTED'
export type Disposition = 'CONFIRMED' | 'DISMISSED' | 'ESCALATED' | 'INFO_REQUESTED'

export interface Principal {
  username: string
  user_id: number | null
  full_name: string
  role: Role
  permissions: string[]
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_at: string
  expires_in: number
  user: Principal
}

export interface RoleMatrix {
  roles: Record<string, string[]>
  descriptions: Record<string, string>
  demo_accounts: { username: string; full_name: string; role: Role }[]
}

export interface Alert {
  id: number
  customer_id: number
  customer_name: string | null
  customer_country: string | null
  customer_industry: string | null
  trigger_event_id: number | null
  trigger_event_type: string | null
  trigger_event_category: string | null
  trigger_event_severity: string | null
  trigger_source: string | null
  previous_tier: Tier
  new_tier: Tier
  previous_score: number
  new_score: number
  previous_log_odds: number
  new_log_odds: number
  status: AlertStatus
  recommended_action: string
  breakdown: Record<string, unknown>
  priority_rank: number | null
  created_at: string
  updated_at: string | null
}

export interface AlertQueue {
  total: number
  page: number
  size: number
  alerts: Alert[]
}

export interface AlertStats {
  total_alerts: number
  open_alerts: number
  by_status: Record<string, number>
  by_tier: Record<string, number>
  critical_open: number
  escalated: number
}

export interface Customer {
  id: number
  name: string
  type: string
  country: string
  industry: string
  expected_turnover: number
  is_pep: boolean
  is_sanctioned: boolean
  onboarding_date: string
  risk_score: number
  risk_tier: Tier
  last_updated: string | null
  aliases: { id: number; alias_name: string; alias_type: string; created_at: string }[]
}

export interface RiskTimelinePoint {
  sequence: number
  timestamp: string
  log_odds: number
  risk_score: number
  risk_tier: Tier
  event_id: number | null
  event_type: string | null
  event_category: string | null
  event_severity: string | null
  likelihood_ratio: number | null
  log_odds_delta: number | null
  label: string
}

export interface RiskTimeline {
  customer_id: number
  customer_name: string
  current_risk_score: number
  current_risk_tier: Tier
  current_log_odds: number
  onboarding_date: string | null
  points: RiskTimelinePoint[]
  tier_thresholds: Record<string, number>
}

export interface RiskExplanationStep {
  step: number
  event_id: number
  event_category: string
  event_severity: string
  event_type: string
  source: string
  likelihood_ratio_LR: number
  log_odds_delta: number
  previous_log_odds: number
  previous_score: number
  new_log_odds: number
  new_score: number
}

export interface RiskExplanation {
  customer_id: number
  customer_name: string
  customer_type: string
  country: string
  industry: string
  is_pep: boolean
  is_sanctioned: boolean
  current_risk_score: number
  current_risk_tier: Tier
  current_log_odds: number
  prior_score: number
  prior_log_odds: number
  onboarding_math_breakdown: {
    equation: string
    intercept: number
    contributions: Record<string, number>
    prior_log_odds: number
    prior_probability: number
    formatted_math: string
  }
  event_history: RiskExplanationStep[]
  step_by_step_math: string[]
  recommendation: string
}

export interface AuditRecord {
  id: number
  sequence_no: number | null
  entity_type: string
  entity_id: number
  customer_id: number | null
  action: string
  action_label: string
  origin: 'SYSTEM' | 'ANALYST'
  actor: string | null
  actor_role: string | null
  details: Record<string, unknown> | null
  prev_hash: string | null
  record_hash: string | null
  created_at: string
}

export interface AuditTrail {
  customer_id: number | null
  customer_name: string | null
  total: number
  page: number
  size: number
  records: AuditRecord[]
  chain_verified: boolean
  head_hash: string
}

export interface ChainVerification {
  chain_valid: boolean
  records_verified: number
  records_in_scope: number
  unchained_legacy_records: number
  head_hash: string
  head_sequence_no: number
  breaks: { record_id: number; sequence_no: number; reason: string }[]
  verified_at: string
  algorithm: string
}

export interface ImmutabilityProof {
  enforced: boolean
  update_blocked: boolean
  delete_blocked: boolean
  update_error: string | null
  delete_error: string | null
  triggers_present: string[]
  method: 'live_probe' | 'trigger_inspection'
  note: string | null
}

export interface PipelineStage {
  stage: string
  status: string
  duration_ms: number
  detail: Record<string, any>
}

export interface PipelineTrace {
  event: {
    id: number
    entity_name: string
    event_type: string
    category: string
    severity: string
    source: string
    created_at: string
    matched_customer_id: number | null
    match_confidence: number
    match_method: string
    matched_customer_name: string | null
  }
  stages: PipelineStage[]
  total_duration_ms: number
  materialized: boolean
  materialized_event_id: number | null
  alert_generated: boolean
  alert_id: number | null
  alert: Alert | null
  audit_records_written: number
  audit_records: AuditRecord[]
}

// ---------------------------------------------------------------- Evaluation

export interface ArmMetrics {
  policy: string
  true_positives: number
  false_positives: number
  false_negatives: number
  true_negatives: number
  material_events_total: number
  immaterial_events_total: number
  detection_rate_recall: number | null
  false_positive_rate: number | null
  precision: number | null
  f1_score: number | null
  specificity: number | null
  latency: {
    count: number
    mean_days: number | null
    median_days: number | null
    p90_days: number | null
    max_days: number | null
  }
  miss_causes: Record<string, number>
  false_positive_causes?: Record<string, number>
  workload: {
    analyst_touches: number
    reviews_scheduled?: number
    productive_reviews?: number
    wasted_reviews?: number
    wasted_review_rate?: number | null
    alerts_raised?: number
    touches_per_material_event_caught: number | null
  }
  measured_pipeline_latency?: {
    mean_microseconds: number | null
    median_microseconds: number | null
    p99_microseconds: number | null
    note: string
  }
  alert_level?: Record<string, any>
}

export interface ThresholdRow {
  fuzzy_threshold: number
  is_operating_point: boolean
  precision: number | null
  recall: number | null
  f1_score: number | null
  correct_matches: number
  wrong_customer_matches: number
  missed_matches: number
  decoys_incorrectly_matched: number
  decoys_correctly_rejected: number
  decoy_rejection_rate: number | null
}

export interface EntityResolutionMetrics {
  precision: number | null
  recall: number | null
  f1_score: number | null
  correct_matches: number
  wrong_customer_matches: number
  missed_matches: number
  decoys_correctly_rejected: number
  decoys_incorrectly_matched: number
  decoy_rejection_rate: number | null
  resolvable_events: number
  matches_attempted: number
  operating_threshold: number
  by_perturbation: Record<
    string,
    { correct: number; wrong: number; missed: number; total: number; accuracy: number | null }
  >
  threshold_sensitivity: ThresholdRow[]
}

export interface EvaluationRun {
  id: number
  label: string
  random_seed: number
  num_customers: number
  num_events: number
  horizon_days: number
  config: {
    num_customers: number
    num_events: number
    horizon_days: number
    baseline_review_interval_days: number
    random_seed: number
    fuzzy_match_threshold: number | null
    assumptions: Record<string, string>
  }
  metrics: {
    baseline: ArmMetrics
    engine: ArmMetrics
    entity_resolution: EntityResolutionMetrics
    deltas: {
      detection_rate_gain: number | null
      false_positive_rate_change: number | null
      median_latency_reduction_days: number | null
      median_latency_speedup_factor: number | null
      additional_material_events_caught: number
      analyst_touch_reduction: number
    }
    ground_truth: {
      total_events: number
      material_events: number
      immaterial_events: number
      material_rate: number
      hard_case_events: number
      decoy_events: number
    }
  }
  chart_data: {
    events_caught: { outcome: string; baseline: number; engine: number }[]
    latency_distribution: { bucket: string; baseline: number; engine: number }[]
    cumulative_detection: { days: number; baseline: number; engine: number }[]
    delay_by_category: {
      category: string
      baseline: number
      engine: number
      material_events: number
    }[]
    workload: { metric: string; baseline: number; engine: number }[]
    entity_resolution_by_perturbation: {
      perturbation: string
      accuracy: number
      total: number
    }[]
    entity_resolution_threshold_sweep: {
      threshold: number
      precision: number
      recall: number
      f1: number
      decoy_rejection: number
      is_operating_point: boolean
    }[]
  }
  runtime_seconds: number
  created_at: string
}
