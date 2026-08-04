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

function socketUrl(runId: string): string {
  const token = tokenStore.access() ?? '';
  const base = API_BASE.startsWith('http')
    ? API_BASE.replace(/^http/, 'ws')
    : `${window.location.origin.replace(/^http/, 'ws')}${API_BASE}`;
  return `${base}/runs/${runId}/stream?token=${encodeURIComponent(token)}`;
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

    const socket = new WebSocket(socketUrl(this.runId));
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
      if (this.retries >= MAX_RETRIES) {
        this.handlers.onStateChange?.('closed');
        return;
      }
      const delay = BASE_DELAY_MS * 2 ** this.retries;
      this.retries += 1;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
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
