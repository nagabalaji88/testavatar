import { Link, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { RunWorkspace } from '@/components/dashboard/RunWorkspace';
import { Button } from '@/components/ui/Button';

export function RunPage() {
  const { runId } = useParams<{ runId: string }>();

  if (!runId) {
    return (
      <div className="grid place-items-center py-24 text-center">
        <p className="text-[13px] text-faint">That run id is not valid.</p>
        <Button asChild size="sm" className="mt-3">
          <Link to="/">Back to dashboard</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Button asChild variant="ghost" size="sm">
        <Link to="/">
          <ArrowLeft className="size-3.5" />
          All runs
        </Link>
      </Button>
      <RunWorkspace runId={runId} />
    </div>
  );
}
