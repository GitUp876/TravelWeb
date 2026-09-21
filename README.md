# TravelWeb

A Django application for selling seats on escorted group trips: guests browse
dated departures, and staff run the trips day to day from an admin that
requires multi-factor authentication.

The full build plan, including the phases still to come, is in the project doc.

## Where phase 1 stands

Built:

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
- Tests, linting, static analysis and a dependency audit, all wired into CI.

Not built yet, by design:

- Taking a booking or a payment. That is phase 2, together with Stripe
  Checkout, webhooks, confirmation emails and the guest's manage-my-booking
  link. Departure pages say so in plain words rather than showing a dead
  button.
- Payment plans are modelled but not yet charged; instalment scheduling is
  phase 3, with manifests, the payments-due report and phone bookings.

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
apps/bookings/       guests, bookings, travellers, payment records
```

## Security notes for anyone changing this

- **No card data belongs in this codebase.** Payments go through Stripe's
  hosted checkout; the only card fields here are brand and last four digits,
  for staff recognition. Adding a card number field would move the site out of
  PCI SAQ A.
- **Guests have no passwords and should not get any.** Access to a booking is
  an expiring signed link to the address on the booking.
- **Totals are recalculated server-side** from each traveller's price option.
  Never trust an amount that arrived in a form.
- **Personal data stays out of the logs.** Dietary and mobility notes are
  health-adjacent; they are redacted from the audit trail and belong only on
  the manifest.
- **`config/settings/prod.py` fails closed**: it refuses to boot without an
  explicit secret key, allowed hosts and a non-guessable admin path.
