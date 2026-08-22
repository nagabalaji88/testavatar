import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as Switch from '@radix-ui/react-switch';
import * as Tabs from '@radix-ui/react-tabs';
import { Cpu, KeyRound, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react';
import type { ProviderConfig } from '@/types';
import { api, ApiError } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { CredentialsPanel } from '@/components/models/CredentialsPanel';
import { ModelCatalog } from '@/components/models/ModelCatalog';
import { ModelFormDialog } from '@/components/models/ModelFormDialog';
import { RegistryImportExport } from '@/components/models/RegistryImportExport';
import { cn, formatNumber } from '@/lib/utils';

/** What supplies this entry's key, in the fewest words that stay actionable.
 *
 *  "Set X" and "key present" are different messages: the first is an
 *  instruction, the second is a confirmation. Collapsing them into one badge
 *  was what let an operator stare at an enabled model that could never run.
 */
function CredentialCell({ model }: { model: ProviderConfig }) {
  if (model.is_local_runtime) {
    return <Badge tone="success">local · no key</Badge>;
  }
  if (!model.credential_available) {
    return (
      <Badge tone="warning">
        set {model.credential_env_var ?? 'API key'}
      </Badge>
    );
  }
  return (
    <Badge tone="success">
      {model.credential_source === 'database'
        ? 'key set here'
        : model.credential_source === 'entry_env'
          ? model.credential_env_var
          : model.credential_source === 'entry_inline'
            ? 'inline key'
            : (model.credential_env_var ?? 'key set')}
    </Badge>
  );
}

function RegistryTable() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<ProviderConfig | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const { data: models = [], isLoading } = useQuery<ProviderConfig[]>({
    queryKey: ['models'],
    queryFn: api.listModels,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['models'] });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleModel(id, enabled),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteModel(id),
    onSuccess: invalidate,
  });

  const mutationError = toggle.error ?? remove.error;

  return (
    <div>
      <SectionHeader
        title="Model registry"
        subtitle="Enable, edit or remove without a deploy. Enabled models with a usable key are the ones the launcher offers."
        icon={<Cpu className="size-4" />}
        action={
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus className="size-3.5" />
            Add model
          </Button>
        }
      />

      {isLoading ? (
        <div className="animate-shimmer h-24 rounded-xl" />
      ) : models.length === 0 ? (
        <p className="rounded-xl bg-white/[0.03] px-4 py-6 text-center text-[12.5px] text-faint ring-1 ring-inset ring-white/10">
          No models yet. Add one, browse what your providers offer, or import a
          JSON file.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-faint">
                <th scope="col" className="pb-2 font-medium">Model</th>
                <th scope="col" className="pb-2 font-medium">Provider</th>
                <th scope="col" className="pb-2 font-medium">Model key</th>
                <th scope="col" className="pb-2 font-medium">Max tokens</th>
                <th scope="col" className="pb-2 font-medium">$ / 1k in</th>
                <th scope="col" className="pb-2 font-medium">$ / 1k out</th>
                <th scope="col" className="pb-2 font-medium">JSON</th>
                <th scope="col" className="pb-2 font-medium">Credential</th>
                <th scope="col" className="pb-2 font-medium">Enabled</th>
                <th scope="col" className="pb-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr
                  key={model.id}
                  className={cn(
                    'border-t border-white/6',
                    // Dimmed rather than hidden: a disabled model still has to
                    // be findable in order to be re-enabled.
                    !model.enabled && 'opacity-55',
                  )}
                >
                  <td className="py-2.5 pr-3 font-medium text-white/90">
                    {model.name}
                  </td>
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
                  <td className="py-2.5 pr-3">
                    <CredentialCell model={model} />
                  </td>
                  <td className="py-2.5 pr-3">
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
                  <td className="py-2.5">
                    <div className="flex items-center gap-1">
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Edit ${model.name}`}
                        onClick={() => {
                          setEditing(model);
                          setFormOpen(true);
                        }}
                      >
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Remove ${model.name}`}
                        onClick={() => remove.mutate(model.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {mutationError ? (
        <p className="mt-3 rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
          {mutationError instanceof ApiError && mutationError.status === 403
            ? 'Only administrators can change the model registry.'
            : mutationError instanceof ApiError
              ? mutationError.message
              : 'That change could not be saved.'}
        </p>
      ) : null}

      <ModelFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
      />

      <div className="mt-6 border-t border-white/6 pt-5">
        <RegistryImportExport registrySize={models.length} />
      </div>
    </div>
  );
}

const TAB_TRIGGER =
  'inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-[12.5px] font-medium text-dim ring-1 ring-inset ring-transparent transition hover:text-white data-[state=active]:bg-white/8 data-[state=active]:text-white data-[state=active]:ring-white/12';

export function ModelsPage() {
  return (
    <GlassCard>
      <Tabs.Root defaultValue="registry">
        <Tabs.List
          aria-label="Model configuration"
          className="mb-5 flex flex-wrap gap-1.5"
        >
          <Tabs.Trigger value="registry" className={TAB_TRIGGER}>
            <Cpu className="size-3.5" />
            Registry
          </Tabs.Trigger>
          <Tabs.Trigger value="catalog" className={TAB_TRIGGER}>
            <Sparkles className="size-3.5" />
            Available models
          </Tabs.Trigger>
          <Tabs.Trigger value="keys" className={TAB_TRIGGER}>
            <KeyRound className="size-3.5" />
            API keys
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="registry">
          <RegistryTable />
        </Tabs.Content>
        <Tabs.Content value="catalog">
          <ModelCatalog />
        </Tabs.Content>
        <Tabs.Content value="keys">
          <CredentialsPanel />
        </Tabs.Content>
      </Tabs.Root>
    </GlassCard>
  );
}
