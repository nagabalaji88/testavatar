/** Shared transport types mirroring the FastAPI schemas. */

export type RunStatus =
  | 'queued'
  | 'analyzing'
  | 'generating'
  | 'evaluating'
  | 'synthesizing'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type CandidateStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out';

export type RiskLevel = 'None' | 'Low' | 'Medium' | 'High';

export type MetricCategory =
  | 'clarity_structure'
  | 'cognitive_quality'
  | 'production_readiness';

export interface MetricDefinition {
  key: string;
  label: string;
  category: MetricCategory;
  weight: number;
  description: string;
}

export interface SecurityAssessment {
  injection_risk: RiskLevel;
  pii_leakage_risk: RiskLevel;
  notes: string[];
}

export interface JudgeVerdict {
  prompt_id: string;
  overall_score: number;
  metrics: Record<string, number>;
  strengths: string[];
  weaknesses: string[];
  missing_elements: string[];
  security_assessment: SecurityAssessment;
  rationale?: string | null;
}

export interface ProviderConfig {
  id: string;
  name: string;
  provider: string;
  model_key: string;
  max_tokens: number;
  cost_per_1k_input: number;
  cost_per_1k_output: number;
  enabled: boolean;
  temperature: number;
  supports_json_mode: boolean;
  api_base?: string | null;
  api_key_env?: string | null;
  weight: number;
  /** Whether the model can actually be called right now. `enabled` only says
   *  an operator wants it; without a key the run drops it. */
  credential_available: boolean;
  /** Which environment variable supplies the key — the name, never the value.
   *  Null for a local runtime, which needs none. */
  credential_env_var?: string | null;
  /** Served from your own hardware, so no key applies. Not a missing key. */
  is_local_runtime: boolean;
  /** The credential family that would make this model callable, so the UI can
   *  link a keyless model straight to the field that fixes it. */
  credential_family?: string | null;
  /** Which of the four sources is actually supplying the key. */
  credential_source?: CredentialSource | null;
}

export type CredentialSource =
  | 'entry_env'
  | 'entry_inline'
  | 'database'
  | 'environment';

/** What POST /models and /models/import accept.
 *
 *  A ProviderConfig minus the four credential-state fields, which the server
 *  computes per request. Sending them back would be meaningless, and typing
 *  them as required here would force every add-a-model form to invent values
 *  for questions only the server can answer. */
export type ModelWritePayload = Omit<
  ProviderConfig,
  | 'credential_available'
  | 'credential_env_var'
  | 'is_local_runtime'
  | 'credential_family'
  | 'credential_source'
>;

/** One provider family's key state. Deliberately never carries the value. */
export interface CredentialStatus {
  family: string;
  label: string;
  env_var: string;
  console_url?: string | null;
  configured: boolean;
  /** The last four characters, to confirm *which* key is stored. */
  last4?: string | null;
  source?: 'database' | 'environment' | null;
  /** A stored row the current encryption key can no longer read. */
  needs_reentry: boolean;
  updated_at?: string | null;
  /** Enabled registry entries this one key would unblock. */
  model_count: number;
  /**
   * False for a variable a registry entry names through api_key_env: there is
   * no family row to store a value against, so the environment is the only
   * place it can be set.
   */
  editable: boolean;
}

export interface CredentialTestResult {
  family: string;
  ok: boolean;
  detail: string;
  model_count: number;
}

/** A model the provider says the configured key can reach. */
export interface DiscoveredModel {
  family: string;
  provider_label: string;
  model_key: string;
  remote_id: string;
  display_name: string;
  cost_per_1k_input?: number | null;
  cost_per_1k_output?: number | null;
  max_tokens?: number | null;
  supports_json_mode: boolean;
  in_registry: boolean;
  registry_id?: string | null;
}

export interface FamilyDiscovery {
  family: string;
  label: string;
  configured: boolean;
  models: DiscoveredModel[];
  error?: string | null;
}

export interface RegistryImportResult {
  mode: 'merge' | 'replace';
  added: string[];
  updated: string[];
  removed: string[];
  total: number;
  providers: ProviderConfig[];
}

export interface RequirementAnalysis {
  task_type: string;
  complexity: 'low' | 'medium' | 'high' | 'extreme';
  reasoning_strategy: 'direct' | 'chain_of_thought' | 'tree_of_thought';
  domain_context: string;
  success_criteria: string[];
  risk_factors: string[];
  required_sections: string[];
  recommended_output_format: string;
  tone: string;
  notes?: string | null;
}

export interface Candidate {
  id: string;
  model_id: string;
  model_name: string;
  provider: string;
  status: CandidateStatus;
  content: string | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  overall_score: number | null;
  metrics: Record<string, number> | null;
  evaluation: JudgeVerdict | null;
}

export interface SectionProvenance {
  section: string;
  source_model_id: string;
  source_model_name: string;
  score: number;
  merged_from: string[];
  strategy:
    | 'adopted'
    | 'merged'
    | 'synthesized'
    | 'deduplicated'
    | 'unanimous'
    | 'reinforced';
  directive_count: number;
  unanimous_count: number;
}

/** A directive the engine added because no candidate model supplied it. */
export interface ReinforcementRecord {
  id: string;
  section: string;
  directive: string;
  rationale: string;
}

export interface ConflictRecord {
  section: string;
  kind: 'semantic' | 'syntactic' | 'structural';
  description: string;
  competing_models: string[];
  resolution: string;
  winner_model_id: string | null;
}

export interface OptimizationReport {
  original_tokens: number;
  optimized_tokens: number;
  tokens_saved: number;
  compression_ratio: number;
  removed_duplicate_lines: number;
  collapsed_whitespace_blocks: number;
  normalized_headings: number;
}

export interface ConsensusPrompt {
  id: string;
  content: string;
  overall_score: number | null;
  metrics: Record<string, number> | null;
  evaluation: JudgeVerdict | null;
  section_provenance: SectionProvenance[];
  conflicts: ConflictRecord[];
  reinforcements: ReinforcementRecord[];
  optimization_report: OptimizationReport | null;
  token_count: number;
  tokens_saved: number;
  improvement_over_best: number | null;
}

export interface RunSummary {
  id: string;
  title: string;
  target_domain: string;
  status: RunStatus;
  total_cost_usd: number;
  duration_ms: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface RunDetail extends RunSummary {
  business_problem: string;
  constraints: string[];
  requirements: string[];
  audience: string | null;
  output_format: string | null;
  selected_model_ids: string[];
  analysis: RequirementAnalysis | null;
  error: string | null;
  total_input_tokens: number;
  total_output_tokens: number;
  trace_id: string | null;
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
}

export interface RunCreatePayload {
  title: string;
  business_problem: string;
  target_domain: string;
  constraints: string[];
  requirements: string[];
  audience?: string;
  output_format?: string;
  model_ids: string[];
}

export interface RunAccepted {
  run_id: string;
  status: RunStatus;
  task_id: string | null;
  websocket_url: string;
}

export interface RunStats {
  total_runs: number;
  total_cost_usd: number;
  avg_duration_ms: number;
  best_score: number;
  by_status: Record<string, number>;
}

export type RunEventType =
  | 'run.queued'
  | 'run.started'
  | 'stage.started'
  | 'stage.completed'
  | 'candidate.started'
  | 'candidate.completed'
  | 'candidate.failed'
  | 'evaluation.completed'
  | 'consensus.completed'
  | 'run.completed'
  | 'run.failed'
  | 'log';

export interface RunEvent {
  id: string;
  run_id: string;
  type: RunEventType;
  payload: Record<string, unknown>;
  emitted_at: string;
}

export interface ExecutionLogEntry {
  id: string;
  stage: string;
  model_id: string | null;
  status: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  attempts: number;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string | null;
  role: 'viewer' | 'engineer' | 'admin';
  is_active: boolean;
  created_at: string;
}
