import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as Switch from '@radix-ui/react-switch';
import { Cpu } from 'lucide-react';
import type { ProviderConfig } from '@/types';
import { api } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatNumber } from '@/lib/utils';

export function ModelsPage() {
  const queryClient = useQueryClient();

  const { data: models = [], isLoading } = useQuery<ProviderConfig[]>({
    queryKey: ['models'],
    queryFn: api.listModels,
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleModel(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['models'] }),
  });

  return (
    <GlassCard>
      <SectionHeader
        title="Model Registry"
        subtitle="Providers are configured declaratively — enable or disable without a deploy."
        icon={<Cpu className="size-4" />}
      />

      {isLoading ? (
        <div className="animate-shimmer h-24 rounded-xl" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-faint">
                <th scope="col" className="pb-2 font-medium">Model</th>
                <th scope="col" className="pb-2 font-medium">Provider</th>
                <th scope="col" className="pb-2 font-medium">Model key</th>
                <th scope="col" className="pb-2 font-medium">Max tokens</th>
                <th scope="col" className="pb-2 font-medium">$ / 1k in</th>
                <th scope="col" className="pb-2 font-medium">$ / 1k out</th>
                <th scope="col" className="pb-2 font-medium">JSON mode</th>
                <th scope="col" className="pb-2 font-medium">Credential</th>
                <th scope="col" className="pb-2 font-medium">Enabled</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id} className="border-t border-white/6">
                  <td className="py-2.5 pr-3 font-medium text-white/90">{model.name}</td>
                  <td className="py-2.5 pr-3 text-dim">{model.provider}</td>
                  <td className="py-2.5 pr-3 font-mono text-[11.5px] text-faint">
                    {model.model_key}
                  </td>
                  <td className="py-2.5 pr-3 font-mono tabular-nums text-dim">
                    {formatNumber(model.max_tokens)}
                  </td>
                  <td className="py-2.5 pr-3 font-mono tabular-nums text-dim">
                    ${model.cost_per_1k_input.toFixed(5)}
                  </td>
                  <td className="py-2.5 pr-3 font-mono tabular-nums text-dim">
                    ${model.cost_per_1k_output.toFixed(5)}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Badge tone={model.supports_json_mode ? 'success' : 'neutral'}>
                      {model.supports_json_mode ? 'native' : 'prompted'}
                    </Badge>
                  </td>
                  {/* Enabled and callable are different things: an enabled
                      model with no key is dropped from the run. */}
                  <td className="py-2.5 pr-3">
                    {model.is_local_runtime ? (
                      <Badge tone="success">local · no key</Badge>
                    ) : model.credential_available ? (
                      <Badge tone="success">
                        {model.credential_env_var ?? 'key set'}
                      </Badge>
                    ) : (
                      <Badge tone="warning">
                        set {model.credential_env_var ?? 'API key'}
                      </Badge>
                    )}
                  </td>
                  <td className="py-2.5">
                    <Switch.Root
                      checked={model.enabled}
                      onCheckedChange={(enabled) =>
                        toggle.mutate({ id: model.id, enabled })
                      }
                      aria-label={`Toggle ${model.name}`}
                      className="relative h-5 w-9 rounded-full bg-white/12 outline-none transition data-[state=checked]:bg-aurora-500"
                    >
                      <Switch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white transition-transform will-change-transform data-[state=checked]:translate-x-[18px]" />
                    </Switch.Root>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {toggle.isError ? (
        <p className="mt-3 rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
          Only administrators can change the model registry.
        </p>
      ) : null}
    </GlassCard>
  );
}
