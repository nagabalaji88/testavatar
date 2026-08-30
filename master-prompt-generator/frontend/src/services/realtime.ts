/**
 * Websocket transport for pipeline events.
 *
 * The socket reconnects with exponential backoff and replays the server-side
 * event backlog on every (re)connect, so a dropped connection never leaves the
 * dashboard with a partial picture.
 */

import type { RunEvent } from '@/types';
import { API_BASE, tokenStore } from '@/services/api';

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

interface RunStreamHandlers {
  onEvent: (event: RunEvent) => void;
  onStateChange?: (state: ConnectionState) => void;
}

const MAX_RETRIES = 6;
const BASE_DELAY_MS = 800;

function wsBase(): string {
  return API_BASE.startsWith('http')
    ? API_BASE.replace(/^http/, 'ws')
    : `${window.location.origin.replace(/^http/, 'ws')}${API_BASE}`;
}

/**
 * Trade the access token for a single-use ticket scoped to this run.
 *
 * A handshake cannot carry an Authorization header, so whatever authenticates
 * the socket ends up in the URL — and URLs reach proxy logs, access logs and
 * browser history. The exchange happens over a normal request, where the
 * header works, so only the narrow credential is ever written to a URL. A
 * fresh one is fetched per connect, including every reconnect, because the
 * server burns each ticket on use.
 */
async function fetchTicket(runId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/runs/${runId}/stream-ticket`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${tokenStore.access() ?? ''}` },
  });
  if (!response.ok) {
    throw new Error(`ticket request failed: ${response.status}`);
  }
  const body = (await response.json()) as { ticket: string };
  return body.ticket;
}

export class RunStream {
  private socket: WebSocket | null = null;
  private retries = 0;
  private timer: number | null = null;
  private disposed = false;

  constructor(
    private readonly runId: string,
    private readonly handlers: RunStreamHandlers,
  ) {}

  connect(): void {
    if (this.disposed) return;
    this.handlers.onStateChange?.('connecting');
    void this.openSocket();
  }

  private async openSocket(): Promise<void> {
    let ticket: string;
    try {
      ticket = await fetchTicket(this.runId);
    } catch {
      // Treat a failed exchange like a dropped socket so the same backoff
      // applies -- a ticket request can fail for the same transient reasons.
      this.scheduleRetry();
      return;
    }
    if (this.disposed) return;

    const socket = new WebSocket(
      `${wsBase()}/runs/${this.runId}/stream?ticket=${encodeURIComponent(ticket)}`,
    );
    this.socket = socket;

    socket.onopen = () => {
      this.retries = 0;
      this.handlers.onStateChange?.('open');
    };

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        this.handlers.onEvent(JSON.parse(message.data) as RunEvent);
      } catch {
        // A malformed frame must not tear down the stream.
      }
    };

    socket.onerror = () => {
      this.handlers.onStateChange?.('error');
    };

    socket.onclose = (event: CloseEvent) => {
      this.socket = null;
      if (this.disposed || event.code === 1000 || event.code === 1008) {
        this.handlers.onStateChange?.('closed');
        return;
      }
      this.scheduleRetry();
    };
  }

  private scheduleRetry(): void {
    if (this.disposed || this.retries >= MAX_RETRIES) {
      this.handlers.onStateChange?.('closed');
      return;
    }
    const delay = BASE_DELAY_MS * 2 ** this.retries;
    this.retries += 1;
    this.timer = window.setTimeout(() => this.connect(), delay);
  }

  close(): void {
    this.disposed = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close(1000, 'client closed');
    this.socket = null;
  }
}
