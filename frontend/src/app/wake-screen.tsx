'use client';

/**
 * The screen a first visitor sees while the free-tier backend starts.
 *
 * It exists because the honest alternative was a lie. Before this, a cold visit
 * showed "Backend unreachable" with a paragraph about BACKEND_ORIGIN being
 * misconfigured -- which was never true. The backend was fine; it was asleep,
 * and the page gave up before it finished waking.
 *
 * So this says what is actually happening, shows the wait, and then loads. The
 * waiting is real and cannot be designed away; what it must not do is pretend
 * to be a fault.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, PlugZap } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { ALREADY_AWAKE_MS, waitForBackend } from './wake';

type WakeState = 'checking' | 'waking' | 'ready' | 'failed';

export interface Wake {
  state: WakeState;
  elapsedMs: number;
  error: string | null;
  retry: () => void;
}

/**
 * Poll until the backend is up.
 *
 * 'checking' covers the first moment so a warm backend -- the overwhelmingly
 * common case once someone is using the app -- never flashes a wait screen on
 * its way to rendering. Only a wait long enough to be noticed is announced.
 */
export function useBackendWake(): Wake {
  const [state, setState] = useState<WakeState>('checking');
  const [elapsedMs, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const startedAt = useRef(Date.now());

  const retry = useCallback(() => {
    setState('checking');
    setError(null);
    setElapsed(0);
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    startedAt.current = Date.now();

    // A ticking clock rather than a spinner alone. An indeterminate spinner for
    // two minutes reads as broken; a number that keeps moving reads as work.
    const tick = setInterval(() => {
      const ms = Date.now() - startedAt.current;
      setElapsed(ms);
      setState((s) => (s === 'checking' && ms > ALREADY_AWAKE_MS ? 'waking' : s));
    }, 250);

    waitForBackend(undefined, controller.signal)
      .then(() => setState('ready'))
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setError(e instanceof Error ? e.message : 'The backend could not be reached.');
        setState('failed');
      })
      .finally(() => clearInterval(tick));

    return () => {
      controller.abort();
      clearInterval(tick);
    };
  }, [attempt]);

  return { state, elapsedMs, error, retry };
}

function seconds(ms: number): string {
  return `${Math.floor(ms / 1000)}s`;
}

/** The waiting and failed screens. Rendered by WakeGate, not usually directly. */
export function WakeScreen({ wake }: { wake: Wake }) {
  const { state, elapsedMs, error, retry } = wake;

  if (state === 'failed') {
    return (
      <div className="mx-auto flex max-w-md flex-col items-start px-6 py-24">
        <PlugZap className="h-5 w-5 text-muted-foreground" />
        <h2 className="mt-4 text-base font-semibold">The backend did not start</h2>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{error}</p>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          This is longer than a cold start has ever taken, so something is
          probably genuinely wrong rather than merely slow.
        </p>
        <Button className="mt-6" size="sm" onClick={retry}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-md flex-col items-start px-6 py-24">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      <h2 className="mt-4 text-base font-semibold">Starting the backend</h2>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
        This demo runs on a free tier that shuts the server down when nobody is
        using it. Waking it takes a couple of minutes. Everything is instant
        afterwards.
      </p>
      <p className="mt-4 text-sm tabular text-muted-foreground">
        Waiting {seconds(elapsedMs)}
      </p>
      {elapsedMs > 150_000 && (
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Longer than usual. It is still starting; this page will load itself
          when it is ready.
        </p>
      )}
    </div>
  );
}

/**
 * Render children once the backend answers.
 *
 * Nothing downstream needs to know about cold starts: by the time children
 * mount, a request will succeed.
 */
export function WakeGate({
  children,
  fallback,
}: {
  children: React.ReactNode;
  fallback?: (wake: Wake) => React.ReactNode;
}) {
  const wake = useBackendWake();

  if (wake.state === 'ready') return <>{children}</>;
  if (wake.state === 'checking') return null;
  return <>{fallback ? fallback(wake) : <WakeScreen wake={wake} />}</>;
}
