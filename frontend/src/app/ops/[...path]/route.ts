/**
 * Operator actions, proxied with the operator credential attached.
 *
 * The backend's operator routes (connect, and later sync and disconnect) require
 * an X-Operator-Key header. That key must never reach a browser, so it is not a
 * NEXT_PUBLIC_ variable and nothing client-side ever sees it: the page calls
 * /ops/..., this handler runs on the server, adds the header from OPERATOR_KEY,
 * and forwards to the backend. On the staging preview Vercel's own login gate is
 * what stands between the internet and this route; on a deployment with no
 * OPERATOR_KEY set (production today) every call answers 404 and the page says
 * operator actions are off. This is the stand-in ADR-0012 names until operator
 * login exists (2.6).
 *
 * Only /connections paths are forwarded. A proxy that attached a credential to
 * any path would be a second, authenticated front door to the whole backend.
 */

import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const BACKEND = process.env.BACKEND_ORIGIN || 'http://localhost:8000';
const ALLOWED = /^connections(\/[A-Za-z0-9-]+)*$/;

async function proxy(req: NextRequest, { params }: { params: { path: string[] } }) {
  const key = process.env.OPERATOR_KEY;
  if (!key) {
    return NextResponse.json(
      { error: 'operator_actions_disabled', detail: 'Operator actions are not enabled on this deployment.' },
      { status: 404 },
    );
  }
  const path = params.path.join('/');
  if (!ALLOWED.test(path)) {
    return NextResponse.json({ error: 'not_proxied', detail: `No operator route at /${path}.` }, { status: 404 });
  }
  const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await req.text();
  const upstream = await fetch(`${BACKEND}/${path}${req.nextUrl.search}`, {
    method: req.method,
    headers: {
      'content-type': req.headers.get('content-type') ?? 'application/json',
      'x-operator-key': key,
    },
    body,
    cache: 'no-store',
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { 'content-type': upstream.headers.get('content-type') ?? 'application/json' },
  });
}

export { proxy as GET, proxy as POST, proxy as DELETE };
