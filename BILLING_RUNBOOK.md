# Billing runbook — Lemon Squeezy Test-mode verification

Everything in this doc is **Test mode**. Keep the Lemon Squeezy store in Test mode,
use a **test** API key, and never use a real card. LS Test mode accepts the test
card `4242 4242 4242 4242`, any future expiry, any CVC/ZIP.

The code path is fully built and unit-tested offline (`tests/test_billing.py`,
38 tests — including the chat research gate reading the purchased cap, LS's
raw-body `X-Signature` HMAC, cancellation grace, marketing-name aliases, and the
deferred-then-recovered path for an unattributed subscription). What this runbook covers is the one thing a sandbox
can't do: a real Lemon Squeezy Test-mode checkout + a real signed webhook flipping
a real user's plan end to end. **Billing is not "done" until section 5 passes.**

---

## 0. What maps to what

| Plan id (API) | Public /pricing name | Prospects / mo | Checkout-able | Limit env |
|---------------|----------------------|----------------|---------------|-----------|
| `free`        | Free                 | 3 (`FREE_PROSPECT_LIMIT`) | no (default) | — |
| `pro`         | Starter              | 50             | yes           | `PLAN_LIMIT_PRO` |
| `max`         | Growth               | 100            | yes           | `PLAN_LIMIT_MAX` |
| `enterprise`  | Enterprise           | 300            | no (sales)    | `PLAN_LIMIT_ENTERPRISE` |

Canonical plan ids are **`pro`** and **`max`** (what `GET /api/billing` reports). The
public `/pricing` page still markets them as **Starter** and **Growth**, so those
names are accepted as aliases everywhere a plan is read, and the legacy
`PLAN_LIMIT_STARTER`/`PLAN_LIMIT_GROWTH` env vars are still honoured as fallbacks.

The *same* limit is enforced server-side: the chat research/write/send gate reads
`billing.limit_for_user(user_id)` (cached on the conversation workspace by
`chat/agent.py`), so buying a plan raises the cap everywhere the product spends
money, not just on the Settings card.

---

## 1. One-time Lemon Squeezy dashboard setup (Test mode)

1. LS Dashboard → toggle **Test mode** (top of the sidebar).
2. **Store**: note the numeric **Store ID** (Settings → Stores) → `LEMONSQUEEZY_STORE_ID`.
3. **Products** → create two subscription products, each with a **monthly** variant:
   - `Saqua Starter` (Pro, 50) → $65/mo → open the variant, copy its numeric
     **variant id** → `LEMON_SQUEEZY_PRO_VARIANT_ID`
   - `Saqua Growth` (Max, 100) → $100/mo → copy the variant id → `LEMON_SQUEEZY_MAX_VARIANT_ID`
   - (Optional yearly variants → `LEMON_SQUEEZY_PRO_YEARLY_VARIANT_ID` / `..._MAX_YEARLY_VARIANT_ID`.)
4. **Settings → API** → create a **test** API key → `LEMONSQUEEZY_API_KEY`.
5. **Settings → Webhooks** → **+** → URL `https://<api-host>/api/billing/webhook`,
   set a **signing secret** → `LEMONSQUEEZY_WEBHOOK_SECRET`, and subscribe to the
   events in section 3.

Put these in the backend `.env` (see `.env.example` "Lemon Squeezy billing" block).
They are backend-only and never sent to the browser.

---

## 2. Local end-to-end (recommended first pass)

LS has no `stripe listen`; the webhook needs a publicly reachable URL, so tunnel to
your local backend:

```bash
# backend env: LEMONSQUEEZY_API_KEY, LEMONSQUEEZY_STORE_ID, the two variant ids,
# LEMONSQUEEZY_WEBHOOK_SECRET, DATABASE_URL empty (SQLite).
python -m automation.migrate          # creates billing_* tables (0002 + 0004 + 0005)
uvicorn server.api:app --port 8000

# second terminal — expose the backend so LS can reach the webhook, then set the
# LS webhook URL (section 1.5) to the tunnel's https URL + /api/billing/webhook.
cloudflared tunnel --url http://localhost:8000   # or: ngrok http 8000
```

Frontend: run `saqua-frontend` pointed at the backend, sign in, go to **Settings**.

---

## 3. The webhook events we handle

The endpoint is `POST /api/billing/webhook` (public; authenticated by LS's HMAC
signature, not a session — verified in `billing/lemonsqueezy_client.verify_signature`).
LS signs the **raw body** with HMAC-SHA256 and sends the **hex digest** in the
`X-Signature` header (no timestamp); the event name is in `X-Event-Name` and again
in `meta.event_name`. Our internal `user_id` + `plan` ride along in
`meta.custom_data` (set at checkout).

| Event | Effect (`billing/webhook.py`) |
|-------|-------------------------------|
| `order_created` | remember the customer↔user map (bridges until the sub event) |
| `subscription_created` | activate the chosen plan + persist customer + portal URL |
| `subscription_updated` / `_resumed` / `_unpaused` | record plan/status/period/portal |
| `subscription_cancelled` | status → `cancelled`; entitled until `ends_at`, then Free |
| `subscription_expired` / `_paused` | status → non-entitling → Free |
| `subscription_payment_success` | record invoice (paid), de-duped on invoice id |
| `subscription_payment_recovered` | record invoice + subscription → `active` |
| `subscription_payment_failed` | record invoice + subscription → `past_due` (→ Free) |

Subscribe to all of the above in the LS webhook. Anything else is acknowledged with
200 and ignored. Every effect is **idempotent** (subscriptions keyed on the LS
subscription id, invoices on the invoice id, and each delivery's `X-Signature`
recorded in `billing_events`), so a **replay** — LS's redelivery on a non-2xx, or a
manual **Resend** from the dashboard — is a no-op.

Entitling statuses are `active` and `on_trial`, **plus** a `cancelled` subscription
whose `ends_at` is still in the future — LS keeps a cancelled sub usable until it
ends, and we honour that paid-through grace window (`billing.store.active_subscription`).
Once `ends_at` passes (or LS sends `subscription_expired`), the user drops to Free.

---

## 4. Test-mode checkout walkthrough

1. Settings → **Your plan** → **Upgrade to Pro**. The browser is redirected to
   Lemon Squeezy's hosted Checkout.
2. Pay with `4242 4242 4242 4242`. LS redirects back to the product's `redirect_url`
   = `/settings?checkout=success`; the page shows the success note and refetches the
   plan. (LS has no "cancel URL": a user who abandons checkout just navigates back,
   so `?checkout=cancel` is only reachable if you wire a link to it — harmless.)
3. Watch the LS webhook log (Settings → Webhooks → your endpoint): you should see
   `order_created` + `subscription_created` delivered and answered `200`.
4. The **Your plan** card now reads **Pro — 50 prospects**. Confirm the API agrees:
   `curl -H "Authorization: Bearer <clerk-jwt>" localhost:8000/api/billing` →
   `{"plan":"pro","prospect_limit":50,...}`.

Repeat with **Max** to confirm **100** (`{"plan":"max","prospect_limit":100}`).

---

## 5. Acceptance checklist (billing is "done" only when ALL pass)

- [ ] **Test checkout completes** and returns to `/settings?checkout=success`.
- [ ] **Webhook verified + processed**: the LS webhook log shows the events answered
      `200`; a bad-signature POST returns `400` (already unit-tested).
- [ ] **Pro receives 50**: after a Pro checkout, `GET /api/billing` →
      `{"plan":"pro","prospect_limit":50}` for that user.
- [ ] **Max receives 100**: after a Max checkout → `{"plan":"max","prospect_limit":100}`.
- [ ] **Limits enforced server-side**: in chat as that user, the research gate uses
      the new cap (not the Free 3). A Free user hits the upgrade prompt at 3; a Max
      user is only blocked at prospect 101. (Path: `chat/tools._plan_limit` →
      `_free_slot_blocked`; covered offline by `EnforcementGateTests`.)
- [ ] **Persistence across refresh + re-sign-in**: reload Settings and sign out/in —
      the plan still reads Pro/Max (it lives in Postgres, not the session).
- [ ] **Manage billing opens the LS portal**: **Manage billing** opens the stored
      `urls.customer_portal`.
- [ ] **Cancellation retains access until `ends_at`**: cancel in the portal →
      `subscription_cancelled`; the user KEEPS the plan until the period end, then a
      later `subscription_expired` (or the passed `ends_at`) drops them to Free.
- [ ] **Payment failure**: a failed renewal fires `subscription_payment_failed` →
      status `past_due`, card shows the "update your card" warning, cap drops to Free.
- [ ] **Upgrade preserves usage**: Pro → Max keeps `prospects_used` (usage lives in
      the per-user store, not the subscription).
- [ ] **Replay safety**: **Resend** a delivered event from the LS dashboard → still
      one subscription row, plan unchanged (already unit-tested; confirm once live).

To force events without waiting for a renewal, use the LS dashboard actions on a
test subscription (cancel, pause, resume) or **Resend** a past delivery.

---

## 6. Deploying the webhook (production)

1. **Settings → Webhooks → Add endpoint** → `https://<api-host>/api/billing/webhook`.
2. Subscribe to the events in section 3 and set the signing secret.
3. Copy the signing secret → `LEMONSQUEEZY_WEBHOOK_SECRET` on the **backend** service
   (Railway), alongside `LEMONSQUEEZY_API_KEY`, `LEMONSQUEEZY_STORE_ID`, and the
   variant ids. These go on the API service, **not** the frontend.
4. Run `python -m automation.migrate` against the production `DATABASE_URL` once so
   `billing_*` (0002/0004) and the `portal_url` column (0005) exist.

---

## 7. Remaining external requirements (cannot be done from code)

- Real **Lemon Squeezy Test-mode API key, store id, products/variants, and webhook
  secret** (sections 1–2).
- A **browser sign-in** to complete the hosted Checkout (section 4).
- A **public webhook URL** (tunnel locally; the Railway host in prod).
- Production **`DATABASE_URL`** (Postgres) for cross-session persistence, and the LS
  env vars set on the **backend** Railway service.
- **Do not** switch the store to live mode or replace Test-mode credentials without
  explicit approval (per the brief).
