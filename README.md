# TravelWeb

A Django application for selling seats on escorted group trips: guests browse
dated departures, and staff run the trips day to day from an admin that
requires multi-factor authentication.

The full build plan, including the phases still to come, is in the project doc.

## Where the build stands

Built in phase 1:

- The data model: trips, dated departures, pickup points, per-person price
  options, guests, bookings, travellers, waitlist entries, payment plans and
  payments.
- The staff admin: create and edit trips, add or duplicate departures, set
  prices, pickup times, deposits and the final-payment date, publish and
  unpublish, and see seats sold against seats left.
- The public site: home page with the next departures, a page per category, a
  trip page with itinerary and dates, and a departure page with prices,
  pickup points and availability.
- Roles (Staff, Manager, Owner) as permission groups, MFA on every staff
  account, an audit trail of staff changes, and a hardened response header set.

Built in phase 2:

- Booking and payment in full: a party-size selector, one form per traveller,
  seat holds, Stripe-hosted checkout, and confirmation by webhook.
- The guest's own booking page, reached by a signed expiring link rather than
  a password, plus a "find my booking" form that emails the link back.
- A confirmation email, and a `release_expired_holds` command that frees seats
  when a guest never finishes paying.

Built in phase 3 so far:

- Payment plans. A guest can pay a deposit now and let the balance follow in
  equal monthly instalments, the last landing on the departure's final-payment
  date. The deposit is taken through Stripe's hosted checkout, which also saves
  the card; instalments are then charged off-session on their due dates. No card
  detail reaches this application at any point.

- The payments-due report. A staff page inside the admin
  (`reports/payments-due/`, linked from the admin header) showing overdue
  instalments, what falls due in the next stretch, and confirmed bookings that
  still owe money with nothing scheduled to collect it, each with a total. It is
  read-only and needs the `bookings.view_scheduledpayment` permission on top of
  the admin's usual MFA.

Not built yet, by design:

- Trip manifests, phone bookings and offline payments are the remaining phase 3
  work.

## How paying works

1. The guest fills in the booking form. Nothing is charged and no card is
   asked for on our pages.
2. Seats are held for `SEAT_HOLD_MINUTES` (20 by default) while the guest is
   at Stripe. The hold and the availability check happen in one transaction
   with the departure row locked, so the last seat cannot be sold twice.
3. The guest pays on Stripe's own hosted page. Card details go from their
   browser to Stripe and never touch this application.
4. Stripe calls the webhook. The signature is verified before anything in the
   body is read; only then is the booking confirmed, the payment recorded and
   the confirmation email sent.
5. If the guest abandons checkout, the hold expires and the seats go back.

The booking page is only offered when both `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` are set. Half-configured counts as off, so a guest can
never reach a checkout we would be unable to confirm; production refuses to
boot in that state.

### Stripe setup

```bash
stripe listen --forward-to localhost:8000/stripe/webhook/   # prints whsec_...
```

Put that signing secret in `STRIPE_WEBHOOK_SECRET`. In production, add an
endpoint in the Stripe dashboard for `https://<host>/stripe/webhook/`
subscribed to `checkout.session.completed`, `checkout.session.expired`,
`payment_intent.succeeded` and `payment_intent.payment_failed`. The last two
confirm and reconcile the off-session instalment charges.

### Scheduled jobs

```bash
python manage.py release_expired_holds    # every few minutes
python manage.py charge_due_instalments    # once or twice a day
```

`charge_due_instalments` charges any payment-plan instalment that has come due,
off-session, against the card the guest saved at their deposit checkout. It is
idempotent per instalment, so running it twice never takes a payment twice; a
declined instalment marks itself and its plan as needing attention rather than
retrying blindly. `--dry-run` lists what is due without charging.

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env            # then edit it
docker compose up -d db         # Postgres on localhost:5432

export DJANGO_SETTINGS_MODULE=config.settings.dev
python manage.py migrate
python manage.py bootstrap_roles        # creates the Staff and Manager groups
python manage.py seed_demo_data         # a couple of realistic trips to look at
python manage.py runserver
```

The site is at http://127.0.0.1:8000/ and the staff admin at the path in
`DJANGO_ADMIN_URL` (`staff/` in development).

### Creating the first staff account

The admin refuses anyone without a registered authenticator, including the
first superuser, so there are two steps:

```bash
python manage.py createsuperuser        # email and password
python manage.py setup_mfa you@example.com
```

`setup_mfa` prints an `otpauth://` URI once. Load it into an authenticator app
and clear it from your terminal history; it is not stored anywhere else.

## Checks

```bash
ruff check . && ruff format --check .   # lint and formatting
pytest                                  # test suite
bandit -c pyproject.toml -r apps config -ll
pip-audit -r requirements/base.txt
python manage.py check --deploy         # production settings audit
```

CI runs all of these on every pull request, plus a check that no migration is
missing.

## Layout

```
config/settings/     base, dev, test and prod settings
apps/core/           audit trail, security headers, the MFA admin site
apps/accounts/       staff users, roles, the setup_mfa command
apps/catalog/        trips, departures, prices, pickups, public pages
apps/bookings/       guests, bookings, travellers, the public booking flow
apps/payments/       the Stripe gateway, checkout, webhook and event log
```

## Security notes for anyone changing this

- **No card data belongs in this codebase.** Payments go through Stripe's
  hosted checkout; the only card fields here are brand and last four digits,
  for staff recognition. Adding a card number field would move the site out of
  PCI SAQ A.
- **Instalments charge a saved card, never a stored one.** The card for a
  payment plan is saved by Stripe at the deposit checkout
  (`setup_future_usage='off_session'`); we keep only the Stripe customer and
  payment-method identifiers and charge against them. The charger refuses to run
  unless the deposit confirmed the booking and those identifiers are present, so
  a plan can never charge a card the guest did not present, and its idempotency
  key is stable per instalment so a repeated run cannot take a payment twice.
- **Guests have no passwords and should not get any.** Access to a booking is
  an expiring signed link to the address on the booking.
- **Totals are recalculated server-side** from each traveller's price option.
  Never trust an amount that arrived in a form.
- **Personal data stays out of the logs.** Dietary and mobility notes are
  health-adjacent; they are redacted from the audit trail and belong only on
  the manifest.
- **`config/settings/prod.py` fails closed**: it refuses to boot without an
  explicit secret key, allowed hosts and a non-guessable admin path.
- **The webhook signature is the security boundary.** `/stripe/webhook/` is a
  public endpoint; nothing in the body is read until Stripe's signature over
  that exact body verifies. Never add a code path that trusts the browser's
  return from checkout instead.
- **The booking link is a credential.** It is signed with the secret key and
  expires. Do not add a page that looks a booking up by reference alone, and
  keep the lookup form's answer identical whether or not a booking matched.
