import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Gavel, Scale, Users } from 'lucide-react';
import type { DebateResult, DebateRound } from '@/types';
import { api, ApiError } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { formatNumber } from '@/lib/utils';

const EXAMPLE = 'Should a seed-stage team build on Postgres or DynamoDB, and why?';

/** One model's contribution, de-anonymised for the reader. */
function ContributionCard({
  label,
  modelName,
  content,
}: {
  label: string;
  modelName: string;
  content: string;
}) {
  return (
    <div className="rounded-xl bg-white/4 p-3 ring-1 ring-inset ring-white/8">
      <div className="mb-2 flex items-center gap-2">
        <Badge tone="neutral">{label}</Badge>
        <span className="text-[12px] font-medium text-white/85">{modelName}</span>
      </div>
      <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-dim">{content}</p>
    </div>
  );
}

function RoundSection({ round }: { round: DebateRound }) {
  if (round.contributions.length === 0 && round.failures.length === 0) return null;

  return (
    <div className="mt-4">
      <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wide text-faint">
        {round.title}
      </h3>
      <div className="grid gap-3">
        {round.contributions.map((contribution) => (
          <ContributionCard
            key={`${round.stage}-${contribution.model_id}`}
            label={contribution.label}
            modelName={contribution.model_name}
            content={contribution.content}
          />
        ))}
        {round.failures.map((failure) => (
          <p
            key={`${round.stage}-${failure.model_id}`}
            className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25"
          >
            {failure.model_name} dropped out — {failure.error}
          </p>
        ))}
      </div>
    </div>
  );
}

function Verdict({ result }: { result: DebateResult }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <GlassCard>
        <SectionHeader
          title="Best Answer"
          subtitle={
            result.judge_fell_back
              ? `The judge (${result.judge_model_name}) failed — showing its revised answer from cross-examination instead.`
              : `Judged by ${result.judge_model_name} after ${result.rounds[0]?.contributions.length ?? 0} models argued it out.`
          }
          icon={<Gavel className="size-4" />}
        />

        <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-white/90">
          {result.final_answer}
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/6 pt-3">
          <Badge tone={result.judge_fell_back ? 'warning' : 'success'}>
            {result.judge_fell_back ? 'fallback answer' : 'judged'}
          </Badge>
          {result.solo_mode ? (
            <Badge tone="warning">solo mode — enable a second model for a real debate</Badge>
          ) : null}
          <span className="text-[11.5px] text-faint">
            {(result.elapsed_ms / 1000).toFixed(1)}s ·{' '}
            {formatNumber(result.input_tokens + result.output_tokens)} tokens · $
            {result.cost_usd.toFixed(4)}
          </span>
        </div>
      </GlassCard>
    </motion.div>
  );
}

export function DebatePage() {
  const [question, setQuestion] = useState('');
  const [showTranscript, setShowTranscript] = useState(false);

  const debate = useMutation({
    mutationFn: (value: string) => api.debate(value),
  });

  const result = debate.data;

  return (
    <div className="grid gap-4">
      <GlassCard>
        <SectionHeader
          title="Multi-Model Debate"
          subtitle="Every enabled model answers, cross-examines the others anonymously, then a judge writes the final answer."
          icon={<Scale className="size-4" />}
        />

        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={4}
          placeholder={EXAMPLE}
          aria-label="Question to debate"
          className="w-full resize-y rounded-xl bg-white/5 p-3 text-[13px] text-white/90 outline-none ring-1 ring-inset ring-white/10 placeholder:text-faint focus:ring-aurora-500/50"
        />

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            onClick={() => debate.mutate(question)}
            disabled={question.trim().length === 0 || debate.isPending}
            loading={debate.isPending}
          >
            <Users className="size-4" />
            Run debate
          </Button>

          {result ? (
            <Button variant="ghost" onClick={() => setShowTranscript((value) => !value)}>
              {showTranscript ? 'Hide transcript' : 'Show transcript'}
            </Button>
          ) : null}

          {debate.isPending ? (
            <span className="text-[12px] text-faint">
              Three rounds of model calls — this takes a while.
            </span>
          ) : null}
        </div>

        {debate.isError ? (
          <p className="mt-3 rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
            {debate.error instanceof ApiError
              ? String(debate.error.detail ?? debate.error.message)
              : 'The debate could not be completed.'}
          </p>
        ) : null}
      </GlassCard>

      {result ? <Verdict result={result} /> : null}

      {result && showTranscript ? (
        <GlassCard>
          <SectionHeader
            title="Transcript"
            subtitle="Models saw each other only as Response A/B/C, in a different order each."
            icon={<Users className="size-4" />}
          />
          {result.rounds.map((round) => (
            <RoundSection key={round.stage} round={round} />
          ))}
        </GlassCard>
      ) : null}
    </div>
  );
}
