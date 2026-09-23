"""Creates the three site pages as unpublished starter drafts.

The drafts give the business something to edit rather than a blank box. They
are general wording, not legal advice, and stay invisible to guests until a
Manager publishes each one. Anything in [square brackets] must be replaced.
"""

from django.db import migrations

DRAFT_NOTE = (
    "[Starter draft. Replace everything in square brackets, check every statement "
    "matches how the business actually works, and have the finished page reviewed "
    "before you tick Published. Delete this paragraph.]"
)

TERMS = f"""{DRAFT_NOTE}

These terms apply to every trip booked with [Business legal name] ("we", "us"), whether online, by phone or in person. Please read them before you book.

## Your booking
- A booking is confirmed when we have received your payment in full, or your deposit if you chose a payment plan, and have emailed your confirmation.
- The person who makes the booking accepts these terms for everyone travelling on it and must be 18 or older.
- Please check your confirmation straight away and tell us about any mistakes in names or boarding points.

## Prices and paying
- Prices are per person and include what is listed as included on the trip page. Anything listed as not included is extra.
- Card payments are taken by our payment provider, Stripe. We never see or store your card number.
- On a payment plan you pay a deposit when you book and the balance in monthly instalments, taken automatically from the card you saved at checkout. The final instalment is due on the final-payment date shown when you booked.
- If an instalment is declined we will contact you. If the balance is not paid by [number] days before departure we may cancel the booking and apply the cancellation charges below.

## If you cancel
Each trip shows its own cancellation policy on the date's page, and that policy applies to your booking. [Or set out your standard charges here, for example: more than 90 days before departure, you lose your deposit; 30 to 90 days before, you lose [percent]% of the total; under 30 days, no refund.]

Please tell us in writing, by email to [email address]. Refunds are made to the card or account you paid with, within [number] days.

## If we change or cancel a trip
- We may need to change an itinerary, hotel or boarding time for reasons outside our control. We will tell you as soon as we can.
- If we cancel a trip, you can choose a full refund of everything you have paid or move to another date.
- [If a trip needs a minimum number of travellers, say so here and how much notice you will give.]

## On the day
- Please be at your boarding point [number] minutes before the boarding time. The coach cannot wait for late passengers.
- Your tour director may refuse to carry anyone whose behaviour puts others at risk, without refund.
- Tell us about dietary or mobility needs when you book. We will pass them to the people who need them but cannot guarantee every supplier can meet them.

## Travel insurance
[We strongly recommend / We require] travel insurance that covers cancellation, medical costs and personal belongings.

## Our responsibility
[Set out your liability terms here, as advised by your lawyer or insurer.]

## Contact
[Business legal name], [postal address]. Phone [phone]. Email [email address].
"""

PRIVACY = f"""{DRAFT_NOTE}

This policy explains what [Business legal name] ("we", "us") collects when you book a trip, why, and what you can ask us to do with it.

## What we collect
- Your name, email address, phone number and postal address, so we can confirm your booking and contact you about it.
- The name, price option and boarding point of each traveller, and an emergency contact.
- Dietary and mobility needs, only if you choose to tell us. We use these only to look after you on the trip.
- A record of what you have paid. Card payments are handled by Stripe; we never see or store your card number, only the card type and last four digits.

## How we use it
- To run your booking: confirmations, reminders, boarding lists and payment receipts.
- To take instalments on a payment plan, using the card you saved with Stripe.
- To tell you about future trips, only if you ticked the box to say so. You can ask us to stop at any time.

## Who we share it with
- Stripe, to take payments.
- Our email provider, to send you booking emails.
- Our website host, which stores the booking system.
- Hotels, venues, coach operators and your tour director, only what they need for your trip, such as names and dietary or mobility needs.

We never sell your details.

## How long we keep it
[State how long you keep booking records, for example: for [number] years after your trip, as required for our accounts, then we delete them.]

## Your choices
You can ask to see the details we hold about you, correct them, or ask us to delete them where we are not required to keep them. Email [email address].

## Cookies
This website uses only the cookies it needs to work, such as keeping the booking form secure. It does not use advertising or tracking cookies.

## Contact
[Business legal name], [postal address]. Email [email address].
"""

CONTACT = f"""{DRAFT_NOTE}

We would love to hear from you, whether you have a question about a trip or want to book by phone.

## Office hours
[Monday to Friday, 9am to 5pm]

## Visit or write to us
[Business legal name]
[Street address]
[Town, State ZIP]
"""

PAGES = [
    ("terms", "Booking terms and conditions", TERMS),
    ("privacy", "Privacy policy", PRIVACY),
    ("contact", "Contact us", CONTACT),
]


def create_drafts(apps, schema_editor):
    SitePage = apps.get_model("catalog", "SitePage")
    for kind, title, body in PAGES:
        SitePage.objects.get_or_create(
            kind=kind, defaults={"title": title, "body": body, "is_published": False}
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_sitepage_sitetext")]

    operations = [migrations.RunPython(create_drafts, migrations.RunPython.noop)]
