"""Frees seats held by bookings that never reached payment.

Run every few minutes from the platform's scheduler. Stripe also tells us when
a checkout expires, but a guest who closes the tab before Stripe notices should
not hold a seat any longer than the hold allows.
"""

from django.core.management.base import BaseCommand

from apps.bookings.services import release_expired_holds


class Command(BaseCommand):
    help = "Expires pending bookings whose seat hold has run out."

    def handle(self, *args, **options):
        released = release_expired_holds()
        self.stdout.write(self.style.SUCCESS(f"Released {released} expired hold(s)."))
