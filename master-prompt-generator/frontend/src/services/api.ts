/** Typed HTTP client for the MPG API, with transparent token refresh. */

import type {
  Candidate,
  ConsensusPrompt,
  CurrentUser,
  ExecutionLogEntry,
  MetricDefinition,
  ProviderConfig,
  RunAccepted,
  RunCreatePayload,
  RunDetail,
  RunEvent,
  RunStats,
  RunSummary,
  TokenPair,
} from '@/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
const ACCESS_KEY = 'mpg.access_token';
const REFRESH_KEY = 'mpg.refresh_token';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const tokenStore = {
  access: (): string | null => localStorage.getItem(ACCESS_KEY),
  refresh: (): string | null => localStorage.getItem(REFRESH_KEY),
  set(pair: TokenPair): void {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear(): void {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

let refreshInFlight: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  const refreshToken = tokenStore.refresh();
  if (!refreshToken) return false;

  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) {
        tokenStore.clear();
        return false;
      }
      tokenStore.set((await response.json()) as TokenPair);
      return true;
    } catch {
      tokenStore.clear();
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retryOn401 = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }
  const token = tokenStore.access();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 401 && retryOn401 && (await attemptRefresh())) {
    return request<T>(path, init, false);
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text();
    }
    const message =
      typeof detail === 'object' && detail !== null && 'detail' in detail
        ? String((detail as { detail: unknown }).detail)
        : `Request failed with status ${response.status}`;
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('Content-Type') ?? '';
  if (!contentType.includes('application/json')) {
    return (await response.text()) as unknown as T;
  }
  return (await response.json()) as T;
}

export const api = {
  async login(email: string, password: string): Promise<TokenPair> {
    const body = new URLSearchParams({ username: email, password });
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!response.ok) {
      throw new ApiError('Invalid email or password', response.status);
    }
    const pair = (await response.json()) as TokenPair;
    tokenStore.set(pair);
    return pair;
  },

  async register(payload: {
    email: string;
    password: string;
    full_name?: string;
  }): Promise<CurrentUser> {
    return request<CurrentUser>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /** Revoke the tokens server-side, then clear them locally.
   *  Clearing alone left a stolen access token usable until it expired. */
  async logout(): Promise<void> {
    const refresh = tokenStore.refresh();
    try {
      await request<void>('/auth/logout', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refresh ?? '' }),
      });
    } catch {
      // Network or already-expired token: local cleanup still must happen.
    } finally {
      tokenStore.clear();
    }
  },

  /** Invalidate every session for the current user. */
  async logoutEverywhere(): Promise<void> {
    try {
      await request<void>('/auth/logout-all', { method: 'POST' });
    } finally {
      tokenStore.clear();
    }
  },

  me: (): Promise<CurrentUser> => request<CurrentUser>('/auth/me'),

  listModels: (): Promise<ProviderConfig[]> => request<ProviderConfig[]>('/models'),

  toggleModel: (id: string, enabled: boolean): Promise<ProviderConfig> =>
    request<ProviderConfig>(`/models/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),

  upsertModel: (payload: ProviderConfig): Promise<ProviderConfig> =>
    request<ProviderConfig>('/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  metricDefinitions: (): Promise<MetricDefinition[]> =>
    request<MetricDefinition[]>('/metrics-definitions'),

  createRun: (payload: RunCreatePayload): Promise<RunAccepted> =>
    request<RunAccepted>('/runs', { method: 'POST', body: JSON.stringify(payload) }),

  listRuns: (limit = 25, offset = 0): Promise<RunSummary[]> =>
    request<RunSummary[]>(`/runs?limit=${limit}&offset=${offset}`),

  getRun: (runId: string): Promise<RunDetail> => request<RunDetail>(`/runs/${runId}`),

  getConsensus: (runId: string): Promise<ConsensusPrompt> =>
    request<ConsensusPrompt>(`/runs/${runId}/consensus`),

  getLogs: (runId: string): Promise<ExecutionLogEntry[]> =>
    request<ExecutionLogEntry[]>(`/runs/${runId}/logs`),

  getEvents: (runId: string): Promise<RunEvent[]> =>
    request<RunEvent[]>(`/runs/${runId}/events`),

  stats: (): Promise<RunStats> => request<RunStats>('/runs/stats'),

  deleteRun: (runId: string): Promise<void> =>
    request<void>(`/runs/${runId}`, { method: 'DELETE' }),

  async exportRun(
    runId: string,
    format: string,
    includeEvaluation = true,
  ): Promise<{ filename: string; content: string }> {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    const token = tokenStore.access();
    if (token) headers.set('Authorization', `Bearer ${token}`);

    const response = await fetch(`${API_BASE}/runs/${runId}/export`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ format, include_evaluation: includeEvaluation }),
    });
    if (!response.ok) {
      throw new ApiError('Export failed', response.status);
    }
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const match = /filename="?([^"]+)"?/.exec(disposition);
    return {
      filename: match?.[1] ?? `master-prompt.${format}`,
      content: await response.text(),
    };
  },
};

/** Sort candidates by score so leaderboards are stable across renders. */
export function rankCandidates(candidates: Candidate[]): Candidate[] {
  return [...candidates].sort(
    (a, b) => (b.overall_score ?? -1) - (a.overall_score ?? -1),
  );
}

export { API_BASE };
