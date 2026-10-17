# 0013 · Operator login: Cognito accounts, server-side sessions, roles

**Status:** Accepted 2026-10-16, with the four open decisions settled (end of this record) · **Date:** 2026-10-16 · **Phase:** 2.6

## Context

Two shared secrets stand in for people today.

- **The operator key** (ADR-0012 §8) gates Connect, Test connection, Sync now,
  Roll back and Disconnect. The staging site's server attaches it; the daily sync
  workflow holds a copy in a GitHub secret. Nothing knows *who* pressed a button:
  every audit row says `operator`. It cannot be revoked for one person, only
  rotated for everyone.
- **Vercel's login gate** on the staging preview is what keeps the internet away
  from those buttons. Production has no such gate, which is why ADR-0012 §8 keeps
  operator actions on staging only, and why production still runs on the
  committed pull rather than the sync (decided 2026-10-16, Deferred in STATUS).

ADR-0012 named both as stand-ins "until operator login exists". This record
designs that login. It is written for the production design; where a free-tier
deployment cannot meet it, §9 names the stand-in.

What it must answer:

- Who may change what the site serves, and how does the system know it is them?
- What does a stolen laptop, a leaked database or a forged request get an attacker?
- How is one person's access removed now, not at the next key rotation?
- How does the scheduler, which is not a person, keep working?
- Is anything readable without an account? (No: §3. A customer's at-risk list is never public.)

## Decision in one paragraph

Accounts live in **AWS Cognito** (one user pool per environment, invite-only,
authenticator-app MFA required). The **backend runs the login flow** exactly as
it runs the Salesforce connect: OAuth 2.0 authorization code with PKCE, `state`
and `nonce`, server-side, single-use state in Postgres. A successful login
creates a **server-side session** in Postgres; the browser holds only an opaque,
`httpOnly` session cookie on the site's own origin. **Roles come from Cognito
groups** (`admin`, `operator`, `viewer`). **Every page and every data route
needs a session**: without one the site shows only its sign-in page. Every
action that changes what the site serves also needs the right role, and is
re-checked against Cognito at the moment it runs. The daily
sync authenticates with **GitHub's own identity token** for that workflow, so no
shared secret is left. The operator key and the `/ops` proxy are deleted.

## 1. Identity provider: Cognito

**Decision: AWS Cognito user pools, Essentials tier, one pool per environment.**

- Hosted: passwords, MFA enrolment, password reset, lockout after failed
  attempts and email verification are Cognito's, not code here. Passwords never
  reach this service: the user types them on Cognito's managed login page.
- Free to 10,000 monthly active users on Lite and Essentials (AWS pricing page,
  checked 2026-10-16). Federated users (Google, SAML) are free only to 50 a
  month, so federation is not used now; it is the route for a customer's own
  identity provider later (§10).
- Same cloud as the master key (ADR-0012 §4): one IAM story, one audit trail
  (CloudTrail), no second vendor holding identities.
- One pool per environment, like every other secret-bearing resource: a staging
  account cannot sign in to production.

**Pool settings, set by hand and recorded in the runbook.** Staging's pool is `us-east-1_sFnyqOnla`, in us-east-1 while the master key is in us-west-2; the region is read from the pool id, and the extra hop is a few tens of milliseconds at sign-in only. self sign-up off
(admins create users), email as the username, authenticator-app (TOTP) MFA
required for every account (SMS is not offered: it costs money and is the
weaker factor), Cognito's default password policy, account recovery by verified
email only, refresh tokens valid 8 hours, ID and access tokens 1 hour, one
**confidential** app client (it has a secret, held only by the backend) with the
authorization-code grant and scopes `openid email`.

## 2. Sessions: the backend is the only OAuth client

**Decision: a backend-for-frontend session. No token ever reaches the browser.**

```
browser ──/api/auth/login──▶ backend ──302──▶ Cognito managed login (password + TOTP)
browser ◀──302 + Set-Cookie── backend ◀──code + state── Cognito
browser ──/api/… + cookie──▶ backend: session → user, role, tenant
```

The site already sends every `/api/*` request to the backend through a
same-origin rewrite (`frontend/next.config.js`). So the backend can set a cookie
the browser stores against the site's own origin, and every later `/api/*`
request carries it back. The flow:

1. `GET /api/auth/login?return=/integrations`: the backend creates `state`,
   `nonce` and a PKCE verifier, stores them in the existing single-use state
   table (ADR-0012 §3, provider `cognito`, ten minutes), and redirects to
   Cognito's authorize endpoint.
2. `GET /api/auth/callback?code&state`: consume the state in one statement,
   exchange the code with the verifier and the client secret, verify the ID
   token (signature against Cognito's published keys, `RS256` only, issuer,
   audience = this app client, `token_use = id`, `nonce`, expiry), and read the
   person: `sub`, email, groups, tenant.
3. Create a session row and set the cookie. Redirect to the `return` path, which
   must be a relative path on this site; anything else goes to `/`.
4. `POST /api/auth/logout`: revoke the refresh token at Cognito, delete the
   session row, clear the cookie, and end the Cognito session too, so the next
   sign-in asks for the password again.

**The cookie:** `__Host-cg_session`, `httpOnly`, `Secure`, `SameSite=Lax`,
`Path=/`. Its value is 32 random bytes; the database stores only their SHA-256,
so a copy of the sessions table opens no session. A new value is issued at every
login (no fixation).

**The session row:** user `sub`, email, groups and tenant at login; created,
last seen and absolute expiry; and the Cognito refresh token, **sealed with the
envelope encryption that already protects the Salesforce token** (ADR-0012 §4,
KMS). Absolute lifetime 8 hours (a working day), idle timeout 1 hour.

**Why not tokens in the browser, and why not a Next.js auth library.** A token in
JavaScript-readable storage is stolen by any XSS; a JWT in a cookie cannot be
revoked before it expires. The OAuth guidance for browser apps recommends a
backend-held session for exactly this. The usual Next.js library, Auth.js, is
still on a beta tag for the App Router version, in security-only maintenance,
and had a fail-open advisory in July 2026 (GHSA-8fpg-xm3f-6cx3). The backend
already runs one OAuth code flow with PKCE, single-use state and sealed tokens,
tested and proven on staging; login is the same shape with a different provider.

## 3. What needs an account

**Decision: everything does.** No company's list of at-risk customers sits on a
public page, and this one should not either. The product is shown by a recorded
walkthrough, not by an open link (decided 2026-10-16).

| Who | Can |
|---|---|
| Anyone, no account | The sign-in page, which links to the walkthrough video. The probes (`/health`, `/ready`) stay open, as ADR-0010 requires |
| `viewer` | Every page and every read, for their own tenant |
| `operator` | Everything a viewer can, plus Sync now, Test connection, Roll back |
| `admin` | Everything an operator can, plus Connect, Reconnect and Disconnect |

A signed-in account with none of the three groups is refused at sign-in: it
would see nothing. Admins create users and assign groups in the Cognito console
for now; a page for it is not part of 2.6 (§10). Buttons render by role.

**Where the gate is.** In the backend, on every route except the probes and the
sign-in routes: the frontend redirecting to a sign-in page is convenience, not
the control, because the backend's own address is reachable directly. A
deployment with no user pool configured (production until step 2.6.4) serves as
it does today and says so on `/health`.

## 4. Enforcement on the backend

- A middleware requires a session on every route but the probes and the
  sign-in routes: **401** without one. One dependency,
  `require_role("operator" | "admin")`, replaces `require_operator` on every
  route that changes state: **403** without the role.
- **Re-checked at the moment of action.** Before any change runs, the backend
  uses the session's sealed refresh token to ask Cognito for fresh tokens. A
  disabled user, a removed account or a global sign-out fails that call, and the
  session is deleted on the spot; the groups in the fresh token replace the
  session's, so a role removed in the console is gone at the next action, not at
  the next login. Reads use the session row alone.
- **Tenant on every action.** The session carries the tenant from Cognito's
  `custom:tenant` attribute (set by an admin; a user cannot change their own, because the app never requests the `aws.cognito.signin.user.admin` scope that would allow it); an action
  on a connection whose `tenant_id` differs is refused. One tenant today,
  `default`, but the check exists from the first commit.
- **CSRF.** `SameSite=Lax` stops cross-site `POST`s carrying the cookie; every
  state-changing route also requires the `Origin` header to be the site's own.
- **Audit names the person.** `actor` becomes `user:<sub>` with the email at the
  time in the detail; new events `login`, `login_refused`, `logout`,
  `session_revoked`. Never a token, by the check that already guards the log.

## 5. The scheduler: GitHub's identity token, not a secret

The daily sync (1.11) is a machine and cannot sign in. Three options:

| Option | Cost | Secret to leak |
|---|---|---|
| **GitHub Actions OIDC token** (chosen) | $0 | None. GitHub signs a short-lived token for that run; the backend verifies it against GitHub's published keys |
| Cognito machine-to-machine client | No free tier; charged per token request | A client secret in GitHub |
| Keep a shared service key | $0 | The key, as today |

The backend accepts a GitHub token **only on** `POST /connections/sync-due`, and
only when its issuer is GitHub Actions, its audience is this deployment
(`churnguard-staging` or `churnguard-production`), its repository is this one,
its workflow is `.github/workflows/sync.yml` on `refs/heads/main`, and its event
is `schedule` or `workflow_dispatch`. A fork, another workflow, another branch
or a token minted for another audience is refused. Audited as
`service:github-actions/sync.yml`. The deploy pipeline already authenticates to
AWS this way (README, threat model), so this is the project's second use of it.

## 6. What is deleted

`OPERATOR_KEY` on Render and Vercel, the `STAGING_OPERATOR_KEY` GitHub secret,
the `/ops/*` proxy route and `require_operator`. The API keys of ADR-0010 are
unchanged: they are for programmatic clients of the API, and production does not
set them on purpose (a public demo cannot ask a visitor for a credential).
Vercel's login gate on staging stays as a second layer.

## 7. Threat model

| Threat | Mitigation |
|---|---|
| Stolen session cookie | `httpOnly` (no script reads it), `Secure`, `__Host-` prefix (no subdomain can set it), 1 hour idle and 8 hour absolute expiry, deletable server-side at once |
| Sessions table leaked | Only hashes of session ids; refresh tokens sealed under KMS |
| Password guessed or phished | Cognito lockout; TOTP MFA required, so a password alone opens nothing |
| Forged login (login CSRF), code interception | `state`, `nonce` and PKCE, single use, ten minutes |
| Forged ID token | Signature against Cognito's keys, `RS256` pinned, issuer, audience, `token_use` and `nonce` checked |
| Open redirect after login | `return` must be a relative path; otherwise `/` |
| Cross-site request riding the cookie | `SameSite=Lax` plus an `Origin` check on every state change |
| Someone signs themselves up | Self sign-up off; an admin creates every account |
| Fired operator keeps access | Disable in Cognito: their next action fails the re-check and their session is deleted |
| Role escalation by editing the session | Roles come from Cognito's signed token, refreshed at every action |
| Forged scheduler call | GitHub token pinned to this repository, workflow, branch, event and audience |
| One tenant acting on another's org | Tenant from a Cognito attribute only an admin can set, checked on every action |

## 8. Cost

$0. Cognito Essentials under 10,000 monthly active users; TOTP MFA has no
charge; Cognito's own email sender for invitations (daily-limited, enough for a
handful of operators; SES is the swap); GitHub's OIDC tokens are free. No new
KMS key: sessions are sealed with each environment's existing master key.
Production's own key, Neon branch and user pool arrive with production itself
(§11, step 2.6.4), the ~$1/month already accepted for the integration.

## 9. Production design against this deployment

| Production | Here | Why |
|---|---|---|
| A custom login domain (`login.<company>`) with its own certificate | Cognito's prefix domain | Free; a domain is cosmetic for a handful of operators |
| SES for invitation and reset email | Cognito's default sender, daily-limited | Free; enough for a few users |
| Threat protection (compromised-credential and risk-based checks) | Not enabled | Plus tier, no free tier |
| An invite page for admins | The Cognito console | 2.6 is the gate, not user management |
| Sessions in a cache (Redis) at volume | Postgres | A handful of sessions; Neon is already there |

## 10. Considered and rejected, or later

- **Keep reads public and gate only the buttons.** Rejected 2026-10-16: the
  audience is a recorded walkthrough, not an open link, and a real customer's
  data is never public. It was the first proposal in this record.
- **Self sign-up creating a tenant.** Later, with onboarding; it needs tenant
  provisioning, which does not exist.
- **Sign in with Google or a customer's SAML/OIDC provider.** Later; federated
  users have a 50-a-month free tier, and an enterprise customer's IdP is a
  per-customer integration.
- **Clerk, Auth0, Better Auth.** Each works. Cognito keeps identities in the same
  account and audit trail as the master key, and the backend-held session needs
  no frontend auth library at all.
- **JWT sessions with no server-side store.** Rejected: they cannot be revoked
  before expiry, which is the property this record exists to fix.

## 11. Plan

| Step | Builds | Seen on staging |
|---|---|---|
| **2.6.1 Sign in** | Staging user pool, app client, groups and the first admin (by hand, clicks given one at a time). Backend `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me`; migration for sessions; cookie through the rewrite (verified first, with a fallback to a route handler if Vercel strips it) | **Sign in** in the header; after sign-in, your email and role. Nothing gated yet |
| **2.6.2 The gate** | A session required on every data route; `require_role` on every state change, re-check at action, tenant check, CSRF origin check, audit names the person; a sign-in page with the video link, buttons by role. The operator key still works alongside, for one step | Signed out: only the sign-in page. Signed in: everything, buttons by role; the audit says `user:…` |
| **2.6.3 Scheduler and deletion** | GitHub OIDC on `sync-due`; the workflow requests its token; `OPERATOR_KEY`, `/ops` and the GitHub secret deleted; runbook | The morning run succeeds with no secret; the key is gone everywhere |
| **2.6.4 Production** | Production user pool, Neon branch, KMS key and IAM user, callback URLs; connect and first sync; churnguard.banik.dev behind sign-in; record | The live site shows its sign-in page; signed in, it says "synced" |

## Decisions, settled 2026-10-16

1. **Every page needs an account** (§3). Proposed the other way; changed because
   the product is shown by video, and a customer's data is never public.
2. **Invite-only accounts.** An admin creates each one.
3. **Authenticator-app MFA required** for every account.
4. **The scheduler signs in with GitHub's identity token** (§5).
