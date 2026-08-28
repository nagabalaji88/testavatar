import { useMemo } from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react';
import type { Candidate } from '@/types';
import type { StageState } from '@/store/useRunStore';
import { cn, formatDuration } from '@/lib/utils';
import { useVizTheme } from '@/lib/viz';

type NodeState = 'pending' | 'active' | 'done' | 'failed';

interface FlowNodeData {
  title: string;
  subtitle: string;
  state: NodeState;
}

const STATE_STYLES: Record<NodeState, string> = {
  pending: 'border-line-2 text-ink-3',
  active: 'border-aurora-400/60 text-ink-strong shadow-[0_0_28px_rgba(89,133,255,0.35)]',
  done: 'border-mint-400/40 text-ink-strong',
  failed: 'border-rose-400/50 text-ink-strong',
};

function StateIcon({ state }: { state: NodeState }) {
  if (state === 'active') return <Loader2 className="size-3.5 animate-spin text-aurora-300" />;
  if (state === 'done') return <CheckCircle2 className="size-3.5 text-mint-400" />;
  if (state === 'failed') return <XCircle className="size-3.5 text-rose-400" />;
  return <CircleDashed className="size-3.5 text-ink-4" />;
}

function FlowNode({ data }: NodeProps<FlowNodeData>) {
  return (
    <div
      className={cn(
        'glass min-w-[168px] rounded-2xl border px-3.5 py-2.5 backdrop-blur-xl',
        STATE_STYLES[data.state],
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-marker-1" />
      <div className="flex items-center gap-2">
        <StateIcon state={data.state} />
        <span className="text-[12.5px] font-medium tracking-tight">{data.title}</span>
      </div>
      <p className="mt-1 pl-5.5 text-[11px] text-dim">{data.subtitle}</p>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-marker-1" />
    </div>
  );
}

const nodeTypes = { mpg: FlowNode };

interface PipelineGraphProps {
  stages: StageState[];
  candidates: Candidate[];
}

/** Live execution topology: analysis → parallel fan-out → judge → consensus. */
export function PipelineGraph({ stages, candidates }: PipelineGraphProps) {
  const viz = useVizTheme();
  const { nodes, edges } = useMemo(() => {
    const stageState = (key: string): NodeState =>
      (stages.find((stage) => stage.key === key)?.status ?? 'pending') as NodeState;

    const laneHeight = 78;
    const fanOutTop = -((candidates.length - 1) * laneHeight) / 2;

    const flowNodes: Node<FlowNodeData>[] = [
      {
        id: 'analysis',
        type: 'mpg',
        position: { x: 0, y: 0 },
        data: {
          title: 'Requirement Analyzer',
          subtitle: 'Seeds model-specific meta-prompts',
          state: stageState('analysis'),
        },
      },
      ...candidates.map<Node<FlowNodeData>>((candidate, index) => ({
        id: `model-${candidate.model_id}`,
        type: 'mpg',
        position: { x: 260, y: fanOutTop + index * laneHeight },
        data: {
          title: candidate.model_name,
          subtitle:
            candidate.status === 'succeeded'
              ? `${formatDuration(candidate.latency_ms)} · ${
                  candidate.overall_score?.toFixed(1) ?? 'scoring'
                }`
              : candidate.status === 'failed'
                ? (candidate.error ?? 'failed').slice(0, 42)
                : 'waiting for tokens',
          state:
            candidate.status === 'succeeded'
              ? 'done'
              : candidate.status === 'failed'
                ? 'failed'
                : candidate.status === 'running'
                  ? 'active'
                  : 'pending',
        },
      })),
      {
        id: 'evaluation',
        type: 'mpg',
        position: { x: 520, y: 0 },
        data: {
          title: 'AI Judge',
          subtitle: '15 weighted criteria',
          state: stageState('evaluation'),
        },
      },
      {
        id: 'consensus',
        type: 'mpg',
        position: { x: 760, y: 0 },
        data: {
          title: 'Consensus Engine',
          subtitle: 'Extract → resolve → merge → optimize',
          state: stageState('consensus'),
        },
      },
      {
        id: 'elite',
        type: 'mpg',
        position: { x: 1030, y: 0 },
        data: {
          title: 'Elite Master Prompt',
          subtitle: 'Deployable artefact',
          state: stageState('completed'),
        },
      },
    ];

    const flowEdges: Edge[] = [
      ...candidates.flatMap((candidate) => [
        {
          id: `analysis-${candidate.model_id}`,
          source: 'analysis',
          target: `model-${candidate.model_id}`,
          animated: candidate.status === 'running',
          style: { stroke: viz.EDGE_COLOR, strokeWidth: 1.5 },
        },
        {
          id: `${candidate.model_id}-evaluation`,
          source: `model-${candidate.model_id}`,
          target: 'evaluation',
          animated: candidate.status === 'running',
          style: { stroke: viz.EDGE_COLOR, strokeWidth: 1.5 },
        },
      ]),
      {
        id: 'evaluation-consensus',
        source: 'evaluation',
        target: 'consensus',
        animated: stageState('consensus') === 'active',
        style: { stroke: viz.EDGE_ACTIVE, strokeWidth: 1.5 },
      },
      {
        id: 'consensus-elite',
        source: 'consensus',
        target: 'elite',
        animated: stageState('consensus') === 'active',
        style: { stroke: viz.EDGE_ACTIVE, strokeWidth: 1.5 },
      },
    ];

    return { nodes: flowNodes, edges: flowEdges };
  }, [stages, candidates, viz]);

  return (
    <div className="glass-inset h-[300px] overflow-hidden rounded-2xl">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll
        zoomOnScroll={false}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={22}
          size={1}
          color={viz.FAINT_LINE}
        />
      </ReactFlow>
    </div>
  );
}
