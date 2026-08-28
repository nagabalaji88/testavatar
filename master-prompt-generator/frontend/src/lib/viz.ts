/**
 * Visualization tokens.
 *
 * Series hues are the first three slots of the validated categorical theme.
 * Validated all-pairs against the dashboard surface (#0a0d1c): worst CVD ΔE
 * 9.4, worst normal-vision ΔE 20.9, all ≥ 3:1 contrast. They are mid-tone by
 * construction and clear 3:1 on the light field too, so they are the one part
 * of this file that does not move with the theme — which also keeps a chart
 * screenshotted in one theme readable when pasted next to the other.
 * Do not extend this list by generating hues — cap the series count instead.
 *
 * Everything else here IS theme-dependent. Recharts and Monaco take concrete
 * colour strings rather than classes, so unlike the rest of the UI these
 * cannot be left to CSS; useVizTheme() resolves them per render instead.
 */

import { scaleLinear } from 'd3-scale';

import { useTheme, type ResolvedTheme } from '@/lib/theme';

/** Categorical slots, assigned in fixed order and never cycled. */
export const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70'] as const;

/** Maximum candidate series drawn on a shared-axis chart. */
export const MAX_SERIES = SERIES_COLORS.length;

export interface VizTheme {
  /** Separator stroke between adjacent marks; matches the card behind them. */
  SURFACE: string;
  /**
   * The consensus prompt is the hero mark, not a fourth category: it is drawn
   * in ink rather than a series hue and carries a heavier stroke, so identity
   * never rests on colour alone.
   */
  CONSENSUS_COLOR: string;
  TEXT_PRIMARY: string;
  TEXT_SECONDARY: string;
  TEXT_MUTED: string;
  GRID_COLOR: string;
  AXIS_COLOR: string;
  /** React Flow's dot grid and the unfilled remainder of a score ring. */
  FAINT_LINE: string;
  /** React Flow edges: idle, then the traversed path. */
  EDGE_COLOR: string;
  EDGE_ACTIVE: string;
}

const VIZ_THEMES: Record<ResolvedTheme, VizTheme> = {
  dark: {
    SURFACE: '#0a0d1c',
    CONSENSUS_COLOR: '#e9edfa',
    TEXT_PRIMARY: 'rgba(233, 237, 250, 0.92)',
    TEXT_SECONDARY: 'rgba(233, 237, 250, 0.62)',
    TEXT_MUTED: 'rgba(233, 237, 250, 0.42)',
    GRID_COLOR: 'rgba(255, 255, 255, 0.10)',
    AXIS_COLOR: 'rgba(255, 255, 255, 0.16)',
    FAINT_LINE: 'rgba(255, 255, 255, 0.09)',
    EDGE_COLOR: 'rgba(255, 255, 255, 0.18)',
    EDGE_ACTIVE: 'rgba(255, 255, 255, 0.22)',
  },
  light: {
    SURFACE: '#f5f7fc',
    CONSENSUS_COLOR: '#0d1120',
    TEXT_PRIMARY: 'rgba(13, 17, 32, 0.92)',
    TEXT_SECONDARY: 'rgba(13, 17, 32, 0.68)',
    TEXT_MUTED: 'rgba(13, 17, 32, 0.48)',
    // Gridlines carry a little more alpha on paper: an ink hairline at 0.10
    // over a pale field reads fainter than a white one over a dark field.
    GRID_COLOR: 'rgba(16, 20, 38, 0.12)',
    AXIS_COLOR: 'rgba(16, 20, 38, 0.22)',
    FAINT_LINE: 'rgba(16, 20, 38, 0.13)',
    EDGE_COLOR: 'rgba(16, 20, 38, 0.22)',
    EDGE_ACTIVE: 'rgba(16, 20, 38, 0.30)',
  },
};

export function useVizTheme(): VizTheme {
  return VIZ_THEMES[useTheme().resolved];
}

/**
 * Single-hue sequential ramp (blue). Magnitude is carried by saturation and
 * depth rather than by contrast against the page, so the same ramp reads
 * correctly on either field.
 */
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

/**
 * Ink colour that stays legible on a given sequential fill. Keyed on the
 * FILL's luminance, not the page's, so it is deliberately theme-independent:
 * a cell at score 20 is dark blue in either theme and wants light ink.
 */
export function inkOn(score: number): string {
  return score >= 62 ? '#05060f' : 'rgba(233, 237, 250, 0.92)';
}

export function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

export const SEQUENTIAL_LEGEND = SEQUENTIAL_STEPS;
