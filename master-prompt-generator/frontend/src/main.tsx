import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker';
import App from '@/App';
import { ThemeProvider } from '@/lib/theme';
import '@/index.css';

// @monaco-editor/react defaults to fetching Monaco from a CDN at runtime;
// point it at the copy already bundled from node_modules instead so the
// editor works offline / behind restrictive network policies. Monaco also
// spins up its core editing logic on a web worker it locates by URL at
// runtime, which Vite can't resolve dynamically -- so the worker itself is
// bundled explicitly and handed back here. Only the base editor worker is
// needed since this app only ever shows read-only markdown/diff views, not
// language services (TS/JSON/CSS) that would need their own workers.
self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};
loader.config({ monaco });

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
