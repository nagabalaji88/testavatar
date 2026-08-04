import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { BarChart3, LayoutGrid, Radar as RadarIcon, Workflow } from 'lucide-react';
import type { MetricDefinition, RunDetail } from '@/types';
import { useRunStore } from '@/store/useRunStore';
import { RunStream } from '@/services/realtime';
import { api } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { RunStatusBadge } from '@/components/ui/Badge';
import { StatTiles } from '@/components/dashboard/StatTiles';
import { PipelineGraph } from '@/components/dashboard/PipelineGraph';
import { CandidateCard } from '@/components/dashboard/CandidateCard';
import { ConsensusPanel } from '@/components/dashboard/ConsensusPanel';
import { EventTimeline } from '@/components/dashboard/EventTimeline';
import { PromptDiffViewer } from '@/components/diff/PromptDiffViewer';
import { ScoreRadar } from '@/components/charts/ScoreRadar';
import { MetricHeatmap } from '@/components/charts/MetricHeatmap';
import { CostLatencyCharts } from '@/components/charts/CostLatencyCharts';
import { cn } from '@/lib/utils';

interface ComparisonDashboardProps {
  runId: string;
}

/**
 * The comparison workspace: live pipeline topology, per-model candidates,
 * rubric visualisations, the consensus artefact and its diff.
 */
export function ComparisonDashboard({ runId }: ComparisonDashboardProps) {
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

  const leaderId = ranked.find((candidate) => candidate.overall_score !== null)?.model_id;

  return (
    <div className="space-y-5">
      <motion.header
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-center justify-between gap-3"
      >
        <div className="min-w-0">
          <h1 className="truncate text-[22px] font-semibold tracking-tight text-white">
            {run?.title ?? 'Consensus run'}
          </h1>
          <p className="mt-0.5 text-[13px] text-dim">
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

      <StatTiles run={run} candidates={ranked} consensus={consensus} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <GlassCard>
          <SectionHeader
            title="Execution Graph"
            subtitle="Fan-out across providers, then judge and synthesis."
            icon={<Workflow className="size-4" />}
          />
          <PipelineGraph stages={stages} candidates={ranked} />

          <ol className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
            {stages.map((stage) => (
              <li key={stage.key} className="flex items-center gap-2 text-[12px]">
                <span
                  className={cn(
                    'size-1.5 rounded-full',
                    stage.status === 'done'
                      ? 'bg-mint-400'
                      : stage.status === 'active'
                        ? 'animate-pulse-ring bg-aurora-400'
                        : stage.status === 'failed'
                          ? 'bg-rose-400'
                          : 'bg-white/25',
                  )}
                />
                <span
                  className={stage.status === 'pending' ? 'text-faint' : 'text-white/80'}
                >
                  {stage.label}
                </span>
                {stage.durationMs ? (
                  <span className="font-mono text-[11px] text-faint">
                    {(stage.durationMs / 1000).toFixed(1)}s
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </GlassCard>

        <EventTimeline events={events} connection={connection} />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <GlassCard>
          <SectionHeader
            title="Rubric Profile"
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
            title="Score Matrix"
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
          title="Cost & Latency"
          subtitle="Each measure gets its own axis — no shared scale."
          icon={<BarChart3 className="size-4" />}
        />
        <CostLatencyCharts candidates={ranked} />
      </GlassCard>

      <section>
        <h2 className="mb-3 text-[15px] font-semibold tracking-tight text-white">
          Model Candidates
        </h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {ranked.map((candidate, index) => (
            <CandidateCard
              key={candidate.model_id}
              candidate={candidate}
              rank={index}
              isLeader={candidate.model_id === leaderId}
            />
          ))}
          {!ranked.length ? (
            <GlassCard className="md:col-span-2 xl:col-span-3">
              <p className="py-8 text-center text-[13px] text-faint">
                Candidates appear here the moment the fan-out begins.
              </p>
            </GlassCard>
          ) : null}
        </div>
      </section>

      <ConsensusPanel consensus={consensus} runId={runId} />

      <PromptDiffViewer candidates={ranked} consensus={consensus} />
    </div>
  );
}
