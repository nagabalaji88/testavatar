/**
 * Visualization tokens.
 *
 * Series hues are the first three slots of the validated categorical theme,
 * stepped for a dark surface. Validated all-pairs against the dashboard surface
 * (#0a0d1c): worst CVD ΔE 9.4, worst normal-vision ΔE 20.9, all ≥ 3:1 contrast.
 * Do not extend this list by generating hues — cap the series count instead.
 */

import { scaleLinear } from 'd3-scale';

export const SURFACE = '#0a0d1c';

/** Categorical slots, assigned in fixed order and never cycled. */
export const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70'] as const;

/** Maximum candidate series drawn on a shared-axis chart. */
export const MAX_SERIES = SERIES_COLORS.length;

/**
 * The consensus prompt is the hero mark, not a fourth category: it is drawn in
 * ink rather than a series hue and carries a heavier stroke, so identity never
 * rests on colour alone.
 */
export const CONSENSUS_COLOR = '#e9edfa';

export const TEXT_PRIMARY = 'rgba(233, 237, 250, 0.92)';
export const TEXT_SECONDARY = 'rgba(233, 237, 250, 0.62)';
export const TEXT_MUTED = 'rgba(233, 237, 250, 0.42)';
export const GRID_COLOR = 'rgba(255, 255, 255, 0.10)';
export const AXIS_COLOR = 'rgba(255, 255, 255, 0.16)';

/** Single-hue sequential ramp (blue), stepped for magnitude on a dark surface. */
const SEQUENTIAL_STEPS = [
  '#0d366b',
  '#184f95',
  '#256abf',
  '#3987e5',
  '#6da7ec',
  '#9ec5f4',
  '#cde2fb',
] as const;

const sequentialScale = scaleLinear<string>()
  .domain([0, 100])
  .range([SEQUENTIAL_STEPS[0], SEQUENTIAL_STEPS[SEQUENTIAL_STEPS.length - 1]])
  .clamp(true);

/** Map a 0-100 score onto the sequential ramp (near-surface = low magnitude). */
export function sequentialColor(score: number): string {
  const index = Math.min(
    SEQUENTIAL_STEPS.length - 1,
    Math.max(0, Math.round((score / 100) * (SEQUENTIAL_STEPS.length - 1))),
  );
  return SEQUENTIAL_STEPS[index];
}

export function sequentialContinuous(score: number): string {
  return sequentialScale(score);
}

/** Ink colour that stays legible on a given sequential fill. */
export function inkOn(score: number): string {
  return score >= 62 ? '#05060f' : TEXT_PRIMARY;
}

export function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

export const SEQUENTIAL_LEGEND = SEQUENTIAL_STEPS;
