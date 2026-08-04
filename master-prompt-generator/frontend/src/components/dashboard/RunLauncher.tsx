import { useMemo, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Rocket, X } from 'lucide-react';
import type { ProviderConfig, RunAccepted, RunCreatePayload } from '@/types';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { api, ApiError } from '@/services/api';
import { cn } from '@/lib/utils';

const inputClass =
  'w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-[13px] text-white placeholder:text-white/30 outline-none transition focus:border-aurora-400/60 focus:bg-white/[0.07]';

function TagInput({
  label,
  placeholder,
  values,
  onChange,
}: {
  label: string;
  placeholder: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  const commit = () => {
    const value = draft.trim();
    if (!value || values.includes(value)) return;
    onChange([...values, value]);
    setDraft('');
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commit();
    }
    if (event.key === 'Backspace' && !draft && values.length) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div>
      <label className="mb-1.5 block text-[12px] font-medium text-dim">{label}</label>
      <input
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        onBlur={commit}
        className={inputClass}
      />
      {values.length ? (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {values.map((value) => (
            <li key={value}>
              <button
                type="button"
                onClick={() => onChange(values.filter((item) => item !== value))}
                className="inline-flex items-center gap-1.5 rounded-full bg-white/8 px-2.5 py-1 text-[11px] text-white/80 ring-1 ring-inset ring-white/12 transition hover:bg-white/12"
              >
                {value}
                <X className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function RunLauncher({ onLaunched }: { onLaunched: (run: RunAccepted) => void }) {
  const [title, setTitle] = useState('');
  const [businessProblem, setBusinessProblem] = useState('');
  const [targetDomain, setTargetDomain] = useState('');
  const [audience, setAudience] = useState('');
  const [outputFormat, setOutputFormat] = useState('markdown');
  const [constraints, setConstraints] = useState<string[]>([]);
  const [requirements, setRequirements] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);

  const { data: models = [] } = useQuery<ProviderConfig[]>({
    queryKey: ['models'],
    queryFn: api.listModels,
    staleTime: 60_000,
  });

  const enabled = useMemo(() => models.filter((model) => model.enabled), [models]);

  const mutation = useMutation({
    mutationFn: (payload: RunCreatePayload) => api.createRun(payload),
    onSuccess: onLaunched,
  });

  const chosen = selected.length ? selected : enabled.map((model) => model.id);
  const valid = title.trim().length >= 3 && businessProblem.trim().length >= 20 && targetDomain.trim().length >= 2;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    mutation.mutate({
      title: title.trim(),
      business_problem: businessProblem.trim(),
      target_domain: targetDomain.trim(),
      constraints,
      requirements,
      audience: audience.trim() || undefined,
      output_format: outputFormat,
      model_ids: chosen,
    });
  };

  return (
    <GlassCard elevated>
      <SectionHeader
        title="New Consensus Run"
        subtitle="Describe the problem; every enabled model drafts a prompt in parallel."
        icon={<Rocket className="size-4" />}
      />

      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="title" className="mb-1.5 block text-[12px] font-medium text-dim">
              Title
            </label>
            <input
              id="title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Claims triage assistant"
              className={inputClass}
              required
              minLength={3}
            />
          </div>
          <div>
            <label htmlFor="domain" className="mb-1.5 block text-[12px] font-medium text-dim">
              Target domain
            </label>
            <input
              id="domain"
              value={targetDomain}
              onChange={(event) => setTargetDomain(event.target.value)}
              placeholder="Insurance operations"
              className={inputClass}
              required
            />
          </div>
        </div>

        <div>
          <label
            htmlFor="problem"
            className="mb-1.5 block text-[12px] font-medium text-dim"
          >
            Business problem
            <span className="ml-2 text-faint">
              {businessProblem.trim().length}/20 minimum characters
            </span>
          </label>
          <textarea
            id="problem"
            value={businessProblem}
            onChange={(event) => setBusinessProblem(event.target.value)}
            rows={5}
            placeholder="Our claims team spends four hours a day triaging inbound FNOL emails…"
            className={cn(inputClass, 'resize-y leading-relaxed')}
            required
            minLength={20}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label
              htmlFor="audience"
              className="mb-1.5 block text-[12px] font-medium text-dim"
            >
              Audience <span className="text-faint">(optional)</span>
            </label>
            <input
              id="audience"
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              placeholder="Senior claims adjusters"
              className={inputClass}
            />
          </div>
          <div>
            <label
              htmlFor="format"
              className="mb-1.5 block text-[12px] font-medium text-dim"
            >
              Desired output format
            </label>
            <select
              id="format"
              value={outputFormat}
              onChange={(event) => setOutputFormat(event.target.value)}
              className={inputClass}
            >
              {['markdown', 'json', 'xml', 'yaml'].map((format) => (
                <option key={format} value={format} className="bg-[#0a0d1c]">
                  {format}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TagInput
            label="Constraints"
            placeholder="Never quote a settlement figure — press Enter"
            values={constraints}
            onChange={setConstraints}
          />
          <TagInput
            label="Prompt requirements"
            placeholder="Must emit strict JSON — press Enter"
            values={requirements}
            onChange={setRequirements}
          />
        </div>

        <div>
          <span className="mb-2 block text-[12px] font-medium text-dim">
            Models ({chosen.length} selected)
          </span>
          <div className="flex flex-wrap gap-2">
            {enabled.map((model) => {
              const active = chosen.includes(model.id);
              return (
                <button
                  key={model.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    setSelected(
                      active
                        ? chosen.filter((id) => id !== model.id)
                        : [...chosen, model.id],
                    )
                  }
                  className={cn(
                    'rounded-xl px-3 py-2 text-[12px] ring-1 ring-inset transition',
                    active
                      ? 'bg-aurora-500/20 text-white ring-aurora-400/45'
                      : 'bg-white/[0.04] text-dim ring-white/10 hover:bg-white/8',
                  )}
                >
                  {model.name}
                  <span className="ml-2 text-faint">{model.provider}</span>
                </button>
              );
            })}
            {!enabled.length ? (
              <p className="text-[12px] text-faint">
                No providers are enabled — add one in the model registry.
              </p>
            ) : null}
          </div>
        </div>

        {mutation.isError ? (
          <p className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
            {mutation.error instanceof ApiError
              ? mutation.error.message
              : 'Failed to launch the run.'}
          </p>
        ) : null}

        <div className="flex items-center justify-between gap-3 pt-1">
          <Badge tone="neutral">
            Fan-out across {chosen.length} model{chosen.length === 1 ? '' : 's'}
          </Badge>
          <Button
            type="submit"
            variant="primary"
            size="lg"
            loading={mutation.isPending}
            disabled={!valid || !chosen.length}
          >
            <Rocket className="size-4" />
            Launch consensus run
          </Button>
        </div>
      </form>
    </GlassCard>
  );
}
