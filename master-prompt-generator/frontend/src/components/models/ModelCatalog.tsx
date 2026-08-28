import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
} from 'lucide-react';
import type { DiscoveredModel, FamilyDiscovery, ModelWritePayload } from '@/types';
import { api, ApiError } from '@/services/api';
import { SectionHeader } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { inputClass } from '@/components/ui/Field';
import { cn, formatNumber } from '@/lib/utils';

/** A registry id from a provider's model id.
 *
 *  Namespaced by family because the same model is served by several providers
 *  ("llama-3.3-70b" on both Groq and OpenRouter) and an unprefixed id would
 *  make adding the second silently overwrite the first.
 */
function registryId(model: DiscoveredModel): string {
  const slug = model.remote_id
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `${model.family}-${slug}`.slice(0, 80);
}

function toPayload(model: DiscoveredModel): ModelWritePayload {
  return {
    id: registryId(model),
    name: model.display_name,
    provider: model.provider_label,
    model_key: model.model_key,
    // The provider reports a context window; the registry field is an output
    // ceiling. Capped so a 1M-token context does not become a licence to
    // decode a million tokens on one call.
    max_tokens: Math.min(model.max_tokens ?? 8192, 16384),
    cost_per_1k_input: model.cost_per_1k_input ?? 0,
    cost_per_1k_output: model.cost_per_1k_output ?? 0,
    enabled: true,
    temperature: 0.4,
    supports_json_mode: model.supports_json_mode,
    api_base: null,
    api_key_env: null,
    weight: 1,
  };
}

function price(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value === 0) return 'free';
  return `$${value.toFixed(5)}`;
}

function ModelRow({ model, onAdded }: { model: DiscoveredModel; onAdded: () => void }) {
  const add = useMutation({
    mutationFn: () => api.upsertModel(toPayload(model)),
    onSuccess: onAdded,
  });

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 border-t border-line-1 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-[12.5px] font-medium text-ink-1">
            {model.display_name}
          </span>
          <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10.5px] text-faint">
            {model.model_key}
          </code>
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-faint">
          <span>
            in {price(model.cost_per_1k_input)} · out {price(model.cost_per_1k_output)}
            <span className="ml-1 text-ink-4">/1k</span>
          </span>
          {model.max_tokens ? <span>{formatNumber(model.max_tokens)} ctx</span> : null}
          {model.supports_json_mode ? <span>json mode</span> : null}
        </p>
      </div>

      {model.in_registry ? (
        <Badge tone="success">
          <Check className="size-3" />
          in registry
        </Badge>
      ) : (
        <Button
          size="sm"
          variant="glass"
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          <Plus className="size-3.5" />
          Add
        </Button>
      )}

      {add.isError ? (
        <p className="w-full text-[11px] text-rose-400">
          {add.error instanceof ApiError ? add.error.message : 'Could not add it.'}
        </p>
      ) : null}
    </li>
  );
}

function FamilySection({
  discovery,
  filter,
  onAdded,
}: {
  discovery: FamilyDiscovery;
  filter: string;
  onAdded: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return discovery.models;
    return discovery.models.filter(
      (model) =>
        model.remote_id.toLowerCase().includes(needle) ||
        model.display_name.toLowerCase().includes(needle),
    );
  }, [discovery.models, filter]);

  // A long list collapsed to a useful default: every provider expanded at once
  // is hundreds of rows, and OpenRouter alone serves several hundred.
  const CAP = 12;
  const shown = expanded || filter.trim() ? matches : matches.slice(0, CAP);
  const hidden = matches.length - shown.length;

  return (
    <section className="rounded-xl bg-surface-1 px-4 py-3.5 ring-1 ring-inset ring-line-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-[13px] font-medium text-ink-1">{discovery.label}</h3>
          {!discovery.configured ? (
            <Badge tone="neutral">no key</Badge>
          ) : discovery.error ? (
            <Badge tone="warning">
              <AlertTriangle className="size-3" />
              unavailable
            </Badge>
          ) : (
            <Badge tone="info">{discovery.models.length} models</Badge>
          )}
        </div>
      </div>

      {!discovery.configured ? (
        <p className="mt-1.5 text-[11.5px] text-faint">
          Add a key on the API keys tab to list what this provider offers.
        </p>
      ) : discovery.error ? (
        // The provider's own words. "Unavailable" would hide the distinction
        // between a rejected key and an exhausted quota, which need opposite
        // fixes.
        <p className="mt-1.5 text-[11.5px] text-amber-400">{discovery.error}</p>
      ) : matches.length === 0 ? (
        <p className="mt-1.5 text-[11.5px] text-faint">
          {filter.trim() ? 'Nothing matches that filter.' : 'No chat models offered.'}
        </p>
      ) : (
        <>
          <ul className="mt-1">
            {shown.map((model) => (
              <ModelRow key={model.model_key} model={model} onAdded={onAdded} />
            ))}
          </ul>
          {hidden > 0 ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-2.5 text-[11.5px] text-aurora-300 transition hover:text-aurora-200"
            >
              Show {hidden} more
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

export function ModelCatalog() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');

  const {
    data: families = [],
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery<FamilyDiscovery[]>({
    queryKey: ['model-catalog'],
    queryFn: () => api.modelCatalog(),
    // The listing endpoints are rate-limited and the answer changes on the
    // order of weeks, so this must not refetch on every tab switch.
    staleTime: 5 * 60_000,
  });

  const onAdded = () => {
    queryClient.invalidateQueries({ queryKey: ['models'] });
    queryClient.invalidateQueries({ queryKey: ['model-catalog'] });
  };

  const total = families.reduce((sum, family) => sum + family.models.length, 0);

  if (error) {
    return (
      <p className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
        {error instanceof ApiError && error.status === 403
          ? 'Only administrators can browse provider catalogues.'
          : 'Could not reach the providers.'}
      </p>
    );
  }

  return (
    <div>
      <SectionHeader
        title="Available models"
        subtitle="Fetched live from each provider, so this is what your keys can actually call — not a list that goes stale."
        icon={<Sparkles className="size-4" />}
        action={
          <Button
            size="sm"
            variant="ghost"
            loading={isFetching && !isLoading}
            onClick={() => api.modelCatalog(true).then(() => refetch())}
          >
            <RefreshCw className="size-3.5" />
            Refresh
          </Button>
        }
      />

      <div className="relative mb-3">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ink-4" />
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={total ? `Filter ${total} models…` : 'Filter models…'}
          className={cn(inputClass, 'pl-9')}
          aria-label="Filter available models"
        />
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-[12px] text-faint">
          <Loader2 className="size-3.5 animate-spin" />
          Asking each provider what it offers…
        </div>
      ) : (
        <div className="space-y-2.5">
          {families.map((discovery) => (
            <FamilySection
              key={discovery.family}
              discovery={discovery}
              filter={filter}
              onAdded={onAdded}
            />
          ))}
        </div>
      )}
    </div>
  );
}
