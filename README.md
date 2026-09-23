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

- Phone bookings and offline payments. Staff can take a booking on a guest's
  behalf from "Phone booking" in the admin header. A staff booking reaches the
  seats held back from the website and dates the website has closed, which is
  what holding seats back is for, and it holds them for `STAFF_HOLD_DAYS` (7 by
  default) rather than the twenty minutes a web checkout gets. Money taken by
  hand — cash, cheque, bank transfer — is recorded against the booking with who
  took it, and confirms it. There is deliberately no field for a card number: a
  guest paying by card pays on Stripe's own page.

- Trip manifests. The passenger list for a departure, reached from the
  "Passenger list" link on the departures list. It groups travellers the way a
  coach loads — by boarding point, in boarding order, with anyone who has no
  pickup at the end — and prints cleanly. Dietary and mobility notes appear on
  this page and nowhere else: the CSV download deliberately leaves them out,
  because that file gets emailed on, and every download is written to the audit
  trail.

- Paying a balance online. A guest whose booking still owes money can settle it
  from their own booking page, on Stripe's hosted checkout like every other
  payment. The amount is the balance this database holds, so nothing posted with
  the form can change what is charged, and paying it stands down any instalments
  the booking's plan was still going to collect.

That completes the phase 3 scope.

Design refresh:

- A new look across the public site and the staff admin: deep sea teal, a
  sunrise coral for the next thing to do, warm sand underneath, with Fraunces
  for headings and Figtree for text. Both fonts are open-source (SIL OFL, the
  licences sit next to the files in `static/fonts/`) and served from our own
  origin. Body text is 17px and every text colour pairing meets WCAG AA.
- Photos staff manage in the admin: a main photo per trip, a photo gallery per
  trip (with a lightbox that needs no JavaScript), and "Site photos" for the
  home page banner and each trip category. Every slot has a built-in
  illustration in `static/img/placeholders/`, so nothing looks empty before
  real photography is uploaded.

## Photos

Staff upload photos on the trip form (main photo and gallery) and under
**Site photos** (home page banner, one per trip category). Each upload is
checked and rewritten before it is stored — see `apps/core/images.py`:

- JPEG, PNG or WebP only, judged by the file's contents rather than its name.
  SVG is refused because it can carry script. Uploads over
  `IMAGE_UPLOAD_MAX_BYTES` (10 MB by default), over 40 megapixels, or under
  640×400 are refused before anything is decoded.
- Every accepted photo is decoded and saved again as a fresh JPEG under a
  random name, in a full size (2000px) and a card size (800px). That strips
  EXIF, including the GPS position phone cameras record.
- Only file names of exactly that shape are ever served from `/media/`, with a
  sandboxing content security policy of their own, and a replaced or deleted
  photo's files are removed from disk.

In production, point `DJANGO_MEDIA_ROOT` at a persistent disk (on Render, a
mounted disk owned by the `app` user). The container's own filesystem is
replaced on every deploy, so photos stored there would disappear.

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
python manage.py send_staff_digest         # once a day, after the morning charge run
```

`charge_due_instalments` charges any payment-plan instalment that has come due,
off-session, against the card the guest saved at their deposit checkout. It is
idempotent per instalment, so running it twice never takes a payment twice; a
declined instalment marks itself and its plan as needing attention rather than
retrying blindly. `--dry-run` lists what is due without charging.

## Deploying

`render.yaml` is a Render Blueprint for the whole production setup: the web
service with its photo disk, a Postgres database with point-in-time recovery,
the three scheduled jobs, and one environment group they all share. In the
Render dashboard choose **New > Blueprint** and pick this repository.

- Render cannot prompt for values inside an environment group, so after the
  first apply add the values listed in `render.yaml` to the `travelweb-shared`
  group, then redeploy. Production refuses to boot until
  `DJANGO_ALLOWED_HOSTS`, `DJANGO_ADMIN_URL` and a secret key are set.
- Migrations run as the web service's pre-deploy command, so a deploy never
  serves new code against an old schema. The image itself never migrates.
- Cron schedules are UTC.
- Upload one photo straight after the first deploy. The image runs as the
  unprivileged `app` user; if the upload fails with a permission error, the
  disk is not writable by that user.

### Alerts

- **Errors.** Set `DJANGO_ADMINS` to a comma-separated list of addresses. Any
  logged error is emailed to them, at most once per fifteen minutes per kind
  of error. The email names the URL pattern that failed and the traceback's
  file and line positions, and nothing else: no request body, no cookies, no
  query string and no local variables, because those carry guests' details and
  their signed booking links. Django's own request-dumping error email is
  switched off in `config/settings/base.py`.
- **Daily digest.** `python manage.py send_staff_digest` emails every active
  Manager and Owner a list of what needs a person: payment plans with a
  declined card, overdue instalments, money held on cancelled or expired
  bookings, overpaid bookings, and Stripe notifications that failed in the
  last day. It lists booking references and amounts only. It sends nothing on
  a quiet day; `--always` sends anyway, which is a quick way to test email.

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
apps/core/           audit trail, security headers, the MFA admin site, photo checks
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
- **A guest may only pay what the server says they owe.** The balance checkout
  is a POST from the booking page and its amount comes from the booking's own
  total; no field in the request is read for it. Only a confirmed booking with
  money outstanding can open one, and money arriving for a booking that was
  cancelled meanwhile is recorded and logged rather than quietly confirming it.
- **Nothing may be collected twice.** Paying a balance — online or by a cheque
  staff record — cancels the instalments still scheduled against it, and the
  instalment charger refuses a booking whose balance is already clear. Both
  sides of that are tested.
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
