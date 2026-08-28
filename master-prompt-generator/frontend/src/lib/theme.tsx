/**
 * Theme choice, resolution and persistence.
 *
 * There are three viewer states, not two. "system" is the default and is
 * deliberately not a third palette: it stamps nothing on <html> and lets
 * prefers-color-scheme decide, so the OS can flip the app mid-session. Only
 * an explicit choice writes data-theme, which is what lets a chosen light
 * beat a dark OS and vice versa.
 *
 * CSS handles the palette on its own -- see index.css. This module exists
 * for the parts that cannot read a CSS variable through a class: the Monaco
 * editor and the Recharts palette both want a concrete value in JS.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type ThemeChoice = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'mpg-theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemTheme(): ResolvedTheme {
  return typeof window !== 'undefined' && window.matchMedia(DARK_QUERY).matches
    ? 'dark'
    : 'light';
}

export function readStoredChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored;
    }
  } catch {
    // Private mode, or site data blocked. Following the OS is the safe default.
  }
  return 'system';
}

/** Stamp (or clear) data-theme. Kept in sync with the pre-paint script in index.html. */
function applyChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', choice);
  }
}

interface ThemeValue {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (next: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => readStoredChoice());
  const [system, setSystem] = useState<ResolvedTheme>(() => systemTheme());

  // While the choice is "system" the OS may flip underneath us, and the JS
  // consumers below will not hear about it from CSS.
  useEffect(() => {
    const media = window.matchMedia(DARK_QUERY);
    const onChange = () => setSystem(media.matches ? 'dark' : 'light');
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    applyChoice(choice);
  }, [choice]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Not persisting is survivable; not applying it is not, so this is
      // deliberately not fatal.
    }
  }, []);

  const value = useMemo<ThemeValue>(
    () => ({
      choice,
      resolved: choice === 'system' ? system : choice,
      setChoice,
    }),
    [choice, system, setChoice],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) {
    throw new Error('useTheme must be used inside a ThemeProvider');
  }
  return value;
}
