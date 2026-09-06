/**
 * Waiting for a sleeping backend, without any single request being long.
 *
 * The backend runs on a free tier that shuts down completely after ~15 minutes
 * idle. Waking it means unpacking a 1.1 GB image and loading the model and the
 * warehouse on a tenth of a CPU: 88 seconds measured on a good day, over 300
 * after a long idle.
 *
 * The obvious approach -- one request with a long timeout -- cannot work, and
 * the reason is worth stating because it is not visible from the client code.
 * There are two ceilings under that cold start and only one of them is ours:
 *
 *   frontend fetch timeout    90s   ours, adjustable
 *   Vercel proxy timeout     ~120s  not ours, and not adjustable on the free
 *                                   plan. Measured 2026-09-06: /api/health
 *                                   returned 502 after exactly 120.19s while
 *                                   the backend itself answered in 0.25s once
 *                                   awake.
 *
 * So raising our own timeout past 120s just moves the failure from a client
 * abort to a gateway 502. Same broken page.
 *
 * Polling with short requests sidesteps both. Each probe is aborted by us after
 * PROBE_TIMEOUT_MS, far below the gateway ceiling, so the proxy never gets the
 * chance to give up. We then ask again. Total waiting time is unbounded by
 * anything except our own deadline.
 *
 * The assumption that makes this work: aborting a client request does not
 * cancel the container boot it triggered. The wake happens at the host's
 * router, and it proceeds whether or not anyone is still holding the
 * connection. Verified against a genuinely cold backend rather than reasoned
 * about -- see tests/ and the branch notes.
 */

const HEALTH_URL = '/api/health';

/** Each probe fails fast. Well under the ~120s gateway ceiling, deliberately. */
export const PROBE_TIMEOUT_MS = 8_000;

/** Gap between probes. Short enough to feel responsive, long enough not to
 *  hammer a service that is busy starting up. */
export const PROBE_INTERVAL_MS = 3_000;

/** When to stop waiting and say so. Longer than the worst cold start measured
 *  (>300s on 2026-09-03), so a give-up here means something is actually wrong
 *  rather than merely slow. */
export const WAKE_DEADLINE_MS = 420_000;

/** Below this, the backend was already awake and no wait screen should flash. */
export const ALREADY_AWAKE_MS = 1_500;

export interface WakeProgress {
  elapsedMs: number;
  attempts: number;
}

/** One probe. Resolves true when the backend answered, false for every other
 *  outcome -- a timeout, a refused connection and a gateway error are the same
 *  thing to a caller who is only asking "is it up yet". */
async function probe(): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const r = await fetch(HEALTH_URL, {
      signal: controller.signal,
      // A cached 200 from a previous visit would report a sleeping backend as
      // awake, and the real load would then fail with no wait screen shown.
      cache: 'no-store',
    });
    return r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Poll until the backend answers, or the deadline passes.
 *
 * Throws on deadline rather than resolving false, so a caller cannot mistake
 * "gave up" for "ready" by ignoring a return value.
 */
export async function waitForBackend(
  onProgress?: (p: WakeProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  const startedAt = Date.now();
  let attempts = 0;

  while (true) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');

    attempts += 1;
    if (await probe()) return;

    const elapsedMs = Date.now() - startedAt;
    onProgress?.({ elapsedMs, attempts });

    if (elapsedMs >= WAKE_DEADLINE_MS) {
      throw new Error(
        `The backend did not respond within ${Math.round(WAKE_DEADLINE_MS / 1000)}s ` +
        `across ${attempts} attempts.`,
      );
    }

    await sleep(PROBE_INTERVAL_MS);
  }
}
