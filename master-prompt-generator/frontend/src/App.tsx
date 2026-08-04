import { useEffect, useState, type ReactNode } from 'react';
import {
  Link,
  Navigate,
  Route,
  BrowserRouter as Router,
  Routes,
  useLocation,
} from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Cpu, LayoutDashboard, LogOut, Sparkles } from 'lucide-react';
import type { CurrentUser } from '@/types';
import { api, ApiError, tokenStore } from '@/services/api';
import { HomePage } from '@/pages/HomePage';
import { RunPage } from '@/pages/RunPage';
import { ModelsPage } from '@/pages/ModelsPage';
import { LoginPage } from '@/pages/LoginPage';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const [checked, setChecked] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let active = true;
    if (!tokenStore.access()) {
      setChecked(true);
      return () => {
        active = false;
      };
    }
    api
      .me()
      .then(() => active && setAuthed(true))
      .catch(() => active && setAuthed(false))
      .finally(() => active && setChecked(true));
    return () => {
      active = false;
    };
  }, []);

  if (!checked) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="animate-shimmer h-2 w-40 rounded-full" />
      </div>
    );
  }
  return authed ? <>{children}</> : <Navigate to="/login" replace />;
}

function NavLink({
  to,
  icon,
  label,
}: {
  to: string;
  icon: ReactNode;
  label: string;
}) {
  const { pathname } = useLocation();
  const active = to === '/' ? pathname === '/' : pathname.startsWith(to);
  return (
    <Link
      to={to}
      className={cn(
        'flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] transition',
        active ? 'bg-white/10 text-white' : 'text-dim hover:bg-white/6 hover:text-white',
      )}
    >
      {icon}
      {label}
    </Link>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { data: user } = useQuery<CurrentUser>({
    queryKey: ['me'],
    queryFn: api.me,
    staleTime: 300_000,
  });

  return (
    <div className="min-h-screen">
      <motion.header
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="sticky top-0 z-40 px-4 pt-4"
      >
        <nav className="glass-elevated mx-auto flex max-w-[1480px] items-center gap-2 rounded-2xl px-3 py-2.5">
          <Link to="/" className="mr-2 flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-xl bg-gradient-to-b from-aurora-400 to-aurora-600 shadow-[0_6px_18px_rgba(31,62,245,0.45)]">
              <Sparkles className="size-4 text-white" />
            </span>
            <span className="hidden text-[14px] font-semibold tracking-tight text-white sm:block">
              Master Prompt Generator
            </span>
          </Link>

          <NavLink to="/" icon={<LayoutDashboard className="size-4" />} label="Runs" />
          <NavLink to="/models" icon={<Cpu className="size-4" />} label="Models" />

          <div className="ml-auto flex items-center gap-3">
            {user ? (
              <span className="hidden text-[12px] text-dim sm:block">
                {user.email}
                <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10.5px] text-white/70">
                  {user.role}
                </span>
              </span>
            ) : null}
            <Button
              size="icon"
              variant="ghost"
              aria-label="Sign out"
              onClick={() => {
                api.logout();
                window.location.href = '/login';
              }}
            >
              <LogOut className="size-4" />
            </Button>
          </div>
        </nav>
      </motion.header>

      <main className="mx-auto max-w-[1480px] px-4 py-6">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Shell>
                  <HomePage />
                </Shell>
              </RequireAuth>
            }
          />
          <Route
            path="/runs/:runId"
            element={
              <RequireAuth>
                <Shell>
                  <RunPage />
                </Shell>
              </RequireAuth>
            }
          />
          <Route
            path="/models"
            element={
              <RequireAuth>
                <Shell>
                  <ModelsPage />
                </Shell>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
