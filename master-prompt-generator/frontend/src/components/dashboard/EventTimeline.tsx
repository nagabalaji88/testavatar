import { AnimatePresence, motion } from 'framer-motion';
import { Activity } from 'lucide-react';
import type { RunEvent, RunEventType } from '@/types';
import type { ConnectionState } from '@/services/realtime';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { cn } from '@/lib/utils';

const DOT_COLOR: Record<RunEventType, string> = {
  'run.queued': 'bg-marker-2',
  'run.started': 'bg-aurora-400',
  'stage.started': 'bg-aurora-400',
  'stage.completed': 'bg-mint-400',
  'candidate.started': 'bg-aurora-300',
  'candidate.completed': 'bg-mint-400',
  'candidate.failed': 'bg-rose-400',
  'evaluation.completed': 'bg-plasma-400',
  'consensus.completed': 'bg-plasma-400',
  'run.completed': 'bg-mint-400',
  'run.failed': 'bg-rose-400',
  log: 'bg-marker-1',
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  idle: 'idle',
  connecting: 'connecting…',
  open: 'live',
  closed: 'closed',
  error: 'error',
};

function describe(event: RunEvent): string {
  const payload = event.payload;
  const model = typeof payload.model_name === 'string' ? payload.model_name : '';
  const stage = typeof payload.stage === 'string' ? payload.stage : '';

  switch (event.type) {
    case 'run.queued':
      return 'Run queued for orchestration';
    case 'run.started':
      return 'Pipeline started';
    case 'stage.started':
      return `Stage started — ${stage}`;
    case 'stage.completed':
      return `Stage completed — ${stage}`;
    case 'candidate.started':
      return `${model} generating`;
    case 'candidate.completed':
      return `${model} returned a candidate`;
    case 'candidate.failed':
      return `${model} failed: ${String(payload.error ?? 'unknown error')}`;
    case 'evaluation.completed':
      return `${model} scored ${Number(payload.overall_score ?? 0).toFixed(1)}`;
    case 'consensus.completed':
      return `Consensus synthesized — ${Number(payload.overall_score ?? 0).toFixed(1)}`;
    case 'run.completed':
      return 'Run completed';
    case 'run.failed':
      return `Run failed: ${String(payload.error ?? 'unknown error')}`;
    default:
      return event.type;
  }
}

export function EventTimeline({
  events,
  connection,
}: {
  events: RunEvent[];
  connection: ConnectionState;
}) {
  const recent = [...events].reverse().slice(0, 40);

  return (
    <GlassCard className="flex h-full flex-col">
      <SectionHeader
        title="Execution Telemetry"
        subtitle="Streamed over the run websocket."
        icon={<Activity className="size-4" />}
        action={
          <span className="flex items-center gap-1.5 text-[11px] text-dim">
            <span
              className={cn(
                'size-1.5 rounded-full',
                connection === 'open'
                  ? 'animate-pulse-ring bg-mint-400'
                  : connection === 'error'
                    ? 'bg-rose-400'
                    : 'bg-marker-2',
              )}
            />
            {CONNECTION_LABEL[connection]}
          </span>
        }
      />

      <ol className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1">
        <AnimatePresence initial={false}>
          {recent.map((event) => (
            <motion.li
              key={event.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.22 }}
              className="flex gap-2.5 text-[12px]"
            >
              <span
                className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', DOT_COLOR[event.type])}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-ink-1">{describe(event)}</p>
                <time
                  dateTime={event.emitted_at}
                  className="text-[10.5px] tabular-nums text-faint"
                >
                  {new Date(event.emitted_at).toLocaleTimeString()}
                </time>
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
        {!recent.length ? (
          <li className="py-8 text-center text-[12px] text-faint">
            No events yet — launch a run to see the pipeline execute.
          </li>
        ) : null}
      </ol>
    </GlassCard>
  );
}
