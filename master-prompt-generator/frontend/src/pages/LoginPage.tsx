import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { KeyRound, Sparkles } from 'lucide-react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { api, ApiError } from '@/services/api';

const inputClass =
  'w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-[13px] text-white placeholder:text-white/30 outline-none transition focus:border-aurora-400/60';

export function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      if (mode === 'register') {
        await api.register({ email, password, full_name: fullName || undefined });
      }
      await api.login(email, password);
      navigate('/', { replace: true });
    } catch (exc) {
      setError(
        exc instanceof ApiError ? exc.message : 'Authentication failed. Try again.',
      );
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        className="w-full max-w-[420px]"
      >
        <GlassCard elevated grain className="p-7">
          <div className="mb-6 flex items-center gap-3">
            <span className="grid size-11 place-items-center rounded-2xl bg-gradient-to-b from-aurora-400 to-aurora-600 shadow-[0_8px_24px_rgba(31,62,245,0.45)]">
              <Sparkles className="size-5 text-white" />
            </span>
            <div>
              <h1 className="text-[17px] font-semibold tracking-tight text-white">
                Master Prompt Generator
              </h1>
              <p className="text-[12px] text-dim">Multi-LLM consensus engineering</p>
            </div>
          </div>

          <form onSubmit={submit} className="space-y-3.5">
            {mode === 'register' ? (
              <div>
                <label htmlFor="name" className="mb-1.5 block text-[12px] text-dim">
                  Full name
                </label>
                <input
                  id="name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  className={inputClass}
                  autoComplete="name"
                />
              </div>
            ) : null}

            <div>
              <label htmlFor="email" className="mb-1.5 block text-[12px] text-dim">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className={inputClass}
                autoComplete="email"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1.5 block text-[12px] text-dim">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={mode === 'register' ? 10 : 1}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className={inputClass}
                autoComplete={
                  mode === 'register' ? 'new-password' : 'current-password'
                }
              />
              {mode === 'register' ? (
                <p className="mt-1 text-[11px] text-faint">Minimum 10 characters.</p>
              ) : null}
            </div>

            {error ? (
              <p className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
                {error}
              </p>
            ) : null}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              className="w-full"
              loading={pending}
            >
              <KeyRound className="size-4" />
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </Button>
          </form>

          <button
            type="button"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
            className="mt-4 w-full text-center text-[12px] text-dim transition hover:text-white"
          >
            {mode === 'login'
              ? 'No account yet? Create one'
              : 'Already registered? Sign in'}
          </button>
        </GlassCard>
      </motion.div>
    </div>
  );
}
