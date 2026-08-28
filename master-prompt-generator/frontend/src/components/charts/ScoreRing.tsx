import { motion } from 'framer-motion';
import { sequentialContinuous, useVizTheme } from '@/lib/viz';
import { cn } from '@/lib/utils';

interface ScoreRingProps {
  score: number;
  label?: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

/**
 * Hero meter for a single 0-100 score. The number is the headline; the ring is
 * the context, so the arc stays recessive and the digits carry the weight.
 */
export function ScoreRing({
  score,
  label,
  size = 132,
  strokeWidth = 8,
  className,
}: ScoreRingProps) {
  const clamped = Math.max(0, Math.min(100, score));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const color = sequentialContinuous(clamped);
  const viz = useVizTheme();

  return (
    <div className={cn('relative grid place-items-center', className)}>
      <svg
        width={size}
        height={size}
        role="img"
        aria-label={`${label ?? 'Score'}: ${clamped.toFixed(1)} out of 100`}
        className="-rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={viz.FAINT_LINE}
          strokeWidth={strokeWidth}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: circumference * (1 - clamped / 100) }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className="font-mono text-[30px] font-semibold leading-none tabular-nums text-ink-strong">
          {clamped.toFixed(1)}
        </span>
        {label ? (
          <span className="mt-1.5 text-[11px] tracking-wide text-faint uppercase">
            {label}
          </span>
        ) : null}
      </div>
    </div>
  );
}
