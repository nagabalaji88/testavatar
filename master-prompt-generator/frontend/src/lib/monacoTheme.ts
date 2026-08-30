/**
 * Monaco themes for both palettes.
 *
 * Monaco paints into a canvas-like DOM of its own and cannot read the app's
 * CSS variables, so it is one of the two places (see viz.ts) where the theme
 * has to be resolved in JS. Both variants keep a fully transparent editor
 * background so the glass card behind them still shows through -- only the
 * ink, the gutter and the diff washes change.
 */

import type { Monaco } from '@monaco-editor/react';
import type { ResolvedTheme } from '@/lib/theme';

/** Read-only markdown view inside a glass card. */
export const SINGLE_THEME = { dark: 'mpg-glass-single', light: 'mpg-paper-single' } as const;
/** Side-by-side diff. */
export const DIFF_THEME = { dark: 'mpg-glass', light: 'mpg-paper' } as const;

export function defineEditorThemes(monaco: Monaco): void {
  monaco.editor.defineTheme(SINGLE_THEME.dark, {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#00000000',
      'editor.lineHighlightBackground': '#ffffff08',
      'editorLineNumber.foreground': '#ffffff2e',
      'editorGutter.background': '#00000000',
      'scrollbarSlider.background': '#ffffff18',
    },
  });

  monaco.editor.defineTheme(SINGLE_THEME.light, {
    base: 'vs',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#00000000',
      'editor.lineHighlightBackground': '#10142608',
      'editorLineNumber.foreground': '#10142655',
      'editorGutter.background': '#00000000',
      'scrollbarSlider.background': '#10142626',
    },
  });

  monaco.editor.defineTheme(DIFF_THEME.dark, {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'e9edfa', background: '0a0d1c' },
      { token: 'comment', foreground: '6b7394', fontStyle: 'italic' },
      { token: 'keyword', foreground: '8eb0ff' },
      { token: 'string', foreground: '3ddbb0' },
    ],
    colors: {
      'editor.background': '#00000000',
      'editor.lineHighlightBackground': '#ffffff0a',
      'editorLineNumber.foreground': '#ffffff33',
      'editorLineNumber.activeForeground': '#ffffff88',
      'editorGutter.background': '#00000000',
      'diffEditor.insertedTextBackground': '#199e7033',
      'diffEditor.removedTextBackground': '#d9592633',
      'diffEditor.insertedLineBackground': '#199e7022',
      'diffEditor.removedLineBackground': '#d9592622',
      'scrollbarSlider.background': '#ffffff18',
    },
  });

  monaco.editor.defineTheme(DIFF_THEME.light, {
    base: 'vs',
    inherit: true,
    rules: [
      // Same hues as the dark variant, stepped down to stay legible on paper.
      { token: '', foreground: '0d1120', background: 'f5f7fc' },
      { token: 'comment', foreground: '5b6280', fontStyle: 'italic' },
      { token: 'keyword', foreground: '2545c2' },
      { token: 'string', foreground: '085c45' },
    ],
    colors: {
      'editor.background': '#00000000',
      'editor.lineHighlightBackground': '#1014260a',
      'editorLineNumber.foreground': '#10142655',
      'editorLineNumber.activeForeground': '#101426aa',
      'editorGutter.background': '#00000000',
      // The added/removed washes are the same two hues; they need more alpha
      // to register against a pale ground than against a dark one.
      'diffEditor.insertedTextBackground': '#199e7040',
      'diffEditor.removedTextBackground': '#d9592640',
      'diffEditor.insertedLineBackground': '#199e7026',
      'diffEditor.removedLineBackground': '#d9592626',
      'scrollbarSlider.background': '#10142626',
    },
  });
}

export function editorTheme(
  names: { dark: string; light: string },
  resolved: ResolvedTheme,
): string {
  return names[resolved];
}
