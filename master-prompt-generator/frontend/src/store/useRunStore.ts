/** Zustand store holding live pipeline state for the active run. */

import { create } from 'zustand';
import type {
  Candidate,
  ConsensusPrompt,
  RunDetail,
  RunEvent,
  RunStatus,
} from '@/types';
import type { ConnectionState } from '@/services/realtime';

export type StageKey =
  | 'analysis'
  | 'generation'
  | 'evaluation'
  | 'consensus'
  | 'completed';

export interface StageState {
  key: StageKey;
  label: string;
  status: 'pending' | 'active' | 'done' | 'failed';
  durationMs: number | null;
}

const STAGE_ORDER: { key: StageKey; label: string }[] = [
  { key: 'analysis', label: 'Requirement Analysis' },
  { key: 'generation', label: 'Parallel Generation' },
  { key: 'evaluation', label: 'AI Judge Evaluation' },
  { key: 'consensus', label: 'Consensus Synthesis' },
  { key: 'completed', label: 'Elite Prompt Ready' },
];

function initialStages(): StageState[] {
  return STAGE_ORDER.map((stage) => ({
    ...stage,
    status: 'pending' as const,
    durationMs: null,
  }));
}

interface RunState {
  runId: string | null;
  run: RunDetail | null;
  status: RunStatus;
  stages: StageState[];
  candidates: Record<string, Candidate>;
  consensus: ConsensusPrompt | null;
  events: RunEvent[];
  connection: ConnectionState;
  error: string | null;

  attach: (run: RunDetail) => void;
  reset: (runId?: string) => void;
  setConnection: (state: ConnectionState) => void;
  applyEvent: (event: RunEvent) => void;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function blankCandidate(modelId: string, modelName: string): Candidate {
  return {
    id: modelId,
    model_id: modelId,
    model_name: modelName,
    provider: '',
    status: 'pending',
    content: null,
    error: null,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    overall_score: null,
    metrics: null,
    evaluation: null,
  };
}

export const useRunStore = create<RunState>((set, get) => ({
  runId: null,
  run: null,
  status: 'queued',
  stages: initialStages(),
  candidates: {},
  consensus: null,
  events: [],
  connection: 'idle',
  error: null,

  attach: (run) =>
    set({
      runId: run.id,
      run,
      status: run.status,
      consensus: run.consensus,
      candidates: Object.fromEntries(
        run.candidates.map((candidate) => [candidate.model_id, candidate]),
      ),
      error: run.error,
    }),

  reset: (runId) =>
    set({
      runId: runId ?? null,
      run: null,
      status: 'queued',
      stages: initialStages(),
      candidates: {},
      consensus: null,
      events: [],
      connection: 'idle',
      error: null,
    }),

  setConnection: (connection) => set({ connection }),

  applyEvent: (event) => {
    const state = get();
    const events = [...state.events.slice(-299), event];
    const payload = event.payload;

    const patch: Partial<RunState> = { events };
    const candidates = { ...state.candidates };
    const stages = state.stages.map((stage) => ({ ...stage }));

    const markStage = (
      key: StageKey,
      status: StageState['status'],
      durationMs?: number,
    ) => {
      const index = stages.findIndex((stage) => stage.key === key);
      if (index === -1) return;
      stages[index].status = status;
      if (durationMs !== undefined) stages[index].durationMs = durationMs;
    };

    switch (event.type) {
      case 'run.queued': {
        const models = Array.isArray(payload.models) ? payload.models : [];
        for (const entry of models as { id?: string; name?: string }[]) {
          if (!entry.id) continue;
          candidates[entry.id] = blankCandidate(entry.id, entry.name ?? entry.id);
        }
        patch.status = 'queued';
        break;
      }
      case 'run.started':
        patch.status = 'analyzing';
        break;
      case 'stage.started':
        markStage(asString(payload.stage) as StageKey, 'active');
        break;
      case 'stage.completed':
        markStage(
          asString(payload.stage) as StageKey,
          'done',
          asNumber(payload.duration_ms),
        );
        break;
      case 'candidate.started': {
        const modelId = asString(payload.model_id);
        const existing =
          candidates[modelId] ??
          blankCandidate(modelId, asString(payload.model_name, modelId));
        candidates[modelId] = { ...existing, status: 'running' };
        patch.status = 'generating';
        break;
      }
      case 'candidate.completed': {
        const modelId = asString(payload.model_id);
        const existing =
          candidates[modelId] ??
          blankCandidate(modelId, asString(payload.model_name, modelId));
        candidates[modelId] = {
          ...existing,
          status: 'succeeded',
          content: asString(payload.content, existing.content ?? ''),
          latency_ms: asNumber(payload.latency_ms),
          cost_usd: asNumber(payload.cost_usd),
          input_tokens: asNumber(payload.input_tokens),
          output_tokens: asNumber(payload.output_tokens),
        };
        break;
      }
      case 'candidate.failed': {
        const modelId = asString(payload.model_id);
        const existing =
          candidates[modelId] ??
          blankCandidate(modelId, asString(payload.model_name, modelId));
        candidates[modelId] = {
          ...existing,
          status: 'failed',
          error: asString(payload.error, 'Generation failed'),
        };
        break;
      }
      case 'evaluation.completed': {
        const modelId = asString(payload.model_id);
        const existing =
          candidates[modelId] ??
          blankCandidate(modelId, asString(payload.model_name, modelId));
        candidates[modelId] = {
          ...existing,
          overall_score: asNumber(payload.overall_score),
          metrics: (payload.metrics as Record<string, number> | null) ?? null,
          evaluation: (payload.evaluation as Candidate['evaluation']) ?? null,
        };
        patch.status = 'evaluating';
        break;
      }
      case 'consensus.completed': {
        patch.consensus = {
          id: 'consensus',
          content: asString(payload.content),
          overall_score: asNumber(payload.overall_score),
          metrics: (payload.metrics as Record<string, number> | null) ?? null,
          evaluation: (payload.evaluation as ConsensusPrompt['evaluation']) ?? null,
          section_provenance:
            (payload.section_provenance as ConsensusPrompt['section_provenance']) ?? [],
          conflicts: (payload.conflicts as ConsensusPrompt['conflicts']) ?? [],
          reinforcements:
            (payload.reinforcements as ConsensusPrompt['reinforcements']) ?? [],
          optimization_report:
            (payload.optimization_report as ConsensusPrompt['optimization_report']) ??
            null,
          token_count: asNumber(payload.token_count),
          tokens_saved: asNumber(payload.tokens_saved),
          improvement_over_best: asNumber(payload.improvement_over_best),
        };
        patch.status = 'synthesizing';
        break;
      }
      case 'run.completed':
        markStage('completed', 'done');
        patch.status = 'completed';
        break;
      case 'run.failed':
        patch.status = 'failed';
        patch.error = asString(payload.error, 'Pipeline failed');
        for (const stage of stages) {
          if (stage.status === 'active') stage.status = 'failed';
        }
        break;
      default:
        break;
    }

    set({ ...patch, candidates, stages });
  },
}));

export const stageOrder = STAGE_ORDER;
