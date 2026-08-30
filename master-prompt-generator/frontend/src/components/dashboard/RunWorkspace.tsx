import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  BarChart3,
  ChevronDown,
  LayoutGrid,
  Radar as RadarIcon,
  Workflow,
} from 'lucide-react';
import type { MetricDefinition, RunDetail } from '@/types';
import { useRunStore } from '@/store/useRunStore';
import { RunStream } from '@/services/realtime';
import { api } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { RunStatusBadge } from '@/components/ui/Badge';
import { RunVerdict } from '@/components/dashboard/RunVerdict';
import { RunProgress } from '@/components/dashboard/RunProgress';
import { ModelLedger } from '@/components/dashboard/ModelLedger';
import { PipelineGraph } from '@/components/dashboard/PipelineGraph';
import { ConsensusPanel } from '@/components/dashboard/ConsensusPanel';
import { EventTimeline } from '@/components/dashboard/EventTimeline';
import { PromptDiffViewer } from '@/components/diff/PromptDiffViewer';
import { ScoreRadar } from '@/components/charts/ScoreRadar';
import { MetricHeatmap } from '@/components/charts/MetricHeatmap';
import { CostLatencyCharts } from '@/components/charts/CostLatencyCharts';
import { cn } from '@/lib/utils';

/**
 * The run workspace, ordered by what someone opening it actually wants.
 *
 * The previous layout stacked eight full-height sections in the order the
 * pipeline produces them, which put the artefact the run exists to make at
 * position seven of eight, roughly four thousand pixels down. It also gave a
 * finished run the same live topology graph as a running one.
 *
 * The order here is: is it done, was it worth it, what did I get, which models
 * earned their cost -- then everything else behind a disclosure. Nothing is
 * removed; the charts, the diff and the event log are all still one click
 * away, they just no longer sit between the reader and the result.
 */
export function RunWorkspace({ runId }: { runId: string }) {
  const {
    run,
    status,
    stages,
    candidates,
    consensus,
    events,
    connection,
    error,
    attach,
    reset,
    applyEvent,
    setConnection,
  } = useRunStore();

  const [showAnalysis, setShowAnalysis] = useState(false);

  const { data: definitions = [] } = useQuery<MetricDefinition[]>({
    queryKey: ['metric-definitions'],
    queryFn: api.metricDefinitions,
    staleTime: Infinity,
  });

  const { data: detail } = useQuery<RunDetail>({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId),
    refetchInterval: (query) => {
      const current = query.state.data?.status;
      return current === 'completed' || current === 'failed' ? false : 6000;
    },
  });

  useEffect(() => {
    reset(runId);
  }, [runId, reset]);

  useEffect(() => {
    if (detail) attach(detail);
  }, [detail, attach]);

  useEffect(() => {
    const stream = new RunStream(runId, {
      onEvent: applyEvent,
      onStateChange: setConnection,
    });
    stream.connect();
    return () => stream.close();
  }, [runId, applyEvent, setConnection]);

  const ranked = useMemo(
    () =>
      Object.values(candidates).sort(
        (a, b) => (b.overall_score ?? -1) - (a.overall_score ?? -1),
      ),
    [candidates],
  );

  const settled = status === 'completed' || status === 'failed';

  return (
    <div className="space-y-4">
      <motion.header
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-start justify-between gap-3"
      >
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-tight tracking-tight text-ink-strong">
            {run?.title ?? 'Consensus run'}
          </h1>
          <p className="mt-1 text-[13px] text-dim">
            {run?.target_domain ?? 'Loading run context…'}
            {run?.analysis
              ? ` · ${run.analysis.complexity} complexity · ${run.analysis.reasoning_strategy.replace(/_/g, ' ')}`
              : ''}
          </p>
        </div>
        <RunStatusBadge status={status} />
      </motion.header>

      {error ? (
        <div className="rounded-2xl bg-rose-400/10 px-4 py-3 text-[13px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
          {error}
        </div>
      ) : null}

      <RunProgress
        stages={stages}
        status={status}
        candidates={ranked}
        runDurationMs={run?.duration_ms}
      />

      <RunVerdict run={run} candidates={ranked} consensus={consensus} />

      {/* The artefact the run exists to produce, before anything that
          describes it. */}
      <ConsensusPanel consensus={consensus} runId={runId} />

      <ModelLedger candidates={ranked} consensus={consensus} />

      {/* Analysis is for the second visit, not the first. Live runs open it by
          default because the event log is the only thing worth watching then. */}
      <section className="glass overflow-hidden rounded-[22px]">
        <button
          type="button"
          onClick={() => setShowAnalysis((open) => !open)}
          aria-expanded={showAnalysis || !settled}
          className="flex w-full items-center gap-3 px-5 py-4 text-left transition hover:bg-surface-2"
        >
          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-aurora-300 ring-1 ring-line-2">
            <BarChart3 className="size-4" />
          </span>
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold tracking-tight text-ink-strong">
              Analysis
            </span>
            <span className="block text-[12.5px] text-ink-3">
              Rubric profile, score matrix, cost and latency, the diff, and the event log.
            </span>
          </span>
          <ChevronDown
            className={cn(
              'ml-auto size-4 shrink-0 text-ink-3 transition-transform',
              (showAnalysis || !settled) && 'rotate-180',
            )}
          />
        </button>

        {showAnalysis || !settled ? (
          <div className="space-y-4 border-t border-line-1 p-5">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <GlassCard>
                <SectionHeader
                  title="Execution graph"
                  subtitle="Fan-out across providers, then judge and synthesis."
                  icon={<Workflow className="size-4" />}
                />
                <PipelineGraph stages={stages} candidates={ranked} />
              </GlassCard>

              <EventTimeline events={events} connection={connection} />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <GlassCard>
                <SectionHeader
                  title="Rubric profile"
                  subtitle="Fifteen weighted criteria, per model and for the consensus."
                  icon={<RadarIcon className="size-4" />}
                />
                <ScoreRadar
                  candidates={ranked}
                  consensus={consensus}
                  definitions={definitions}
                />
              </GlassCard>

              <GlassCard>
                <SectionHeader
                  title="Score matrix"
                  subtitle="Where each model wins and loses, criterion by criterion."
                  icon={<LayoutGrid className="size-4" />}
                />
                <MetricHeatmap
                  candidates={ranked}
                  consensus={consensus}
                  definitions={definitions}
                />
              </GlassCard>
            </div>

            <GlassCard>
              <SectionHeader
                title="Cost and latency"
                subtitle="Each measure gets its own axis — no shared scale."
                icon={<BarChart3 className="size-4" />}
              />
              <CostLatencyCharts candidates={ranked} />
            </GlassCard>

            {/* Brings its own card and header. */}
            <PromptDiffViewer candidates={ranked} consensus={consensus} />
          </div>
        ) : null}
      </section>
    </div>
  );
}
