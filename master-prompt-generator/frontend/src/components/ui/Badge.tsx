import type { ReactNode } from 'react';
import { cn, scoreBand } from '@/lib/utils';
import type { CandidateStatus, RiskLevel, RunStatus } from '@/types';

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-white/8 text-white/70 ring-white/12',
  info: 'bg-aurora-400/15 text-aurora-300 ring-aurora-400/30',
  success: 'bg-mint-400/15 text-mint-400 ring-mint-400/30',
  warning: 'bg-amber-400/15 text-amber-400 ring-amber-400/30',
  danger: 'bg-rose-400/15 text-rose-400 ring-rose-400/30',
  accent: 'bg-plasma-400/15 text-plasma-400 ring-plasma-400/30',
};

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium tracking-tight ring-1 ring-inset',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const RUN_STATUS_TONE: Record<RunStatus, Tone> = {
  queued: 'neutral',
  analyzing: 'info',
  generating: 'info',
  evaluating: 'accent',
  synthesizing: 'accent',
  completed: 'success',
  failed: 'danger',
  cancelled: 'warning',
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const pulsing = !['completed', 'failed', 'cancelled'].includes(status);
  return (
    <Badge tone={RUN_STATUS_TONE[status]}>
      <span
        className={cn(
          'size-1.5 rounded-full bg-current',
          pulsing && 'animate-pulse-ring',
        )}
      />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </Badge>
  );
}

const CANDIDATE_TONE: Record<CandidateStatus, Tone> = {
  pending: 'neutral',
  running: 'info',
  succeeded: 'success',
  failed: 'danger',
  timed_out: 'warning',
};

export function CandidateStatusBadge({ status }: { status: CandidateStatus }) {
  return <Badge tone={CANDIDATE_TONE[status]}>{status.replace('_', ' ')}</Badge>;
}

const RISK_TONE: Record<RiskLevel, Tone> = {
  None: 'success',
  Low: 'success',
  Medium: 'warning',
  High: 'danger',
};

export function RiskBadge({ label, level }: { label: string; level: RiskLevel }) {
  return (
    <Badge tone={RISK_TONE[level]}>
      {label}: {level}
    </Badge>
  );
}

export function ScoreBadge({ score }: { score: number }) {
  const band = scoreBand(score);
  const tone: Tone =
    band === 'elite'
      ? 'success'
      : band === 'strong'
        ? 'info'
        : band === 'fair'
          ? 'warning'
          : 'danger';
  return <Badge tone={tone}>{score.toFixed(1)}</Badge>;
}
