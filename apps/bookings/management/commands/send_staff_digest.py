"""Emails Managers a list of what needs attention.

Run once a day from the platform's scheduler, after the morning instalment
run. Sends nothing on a day with nothing to report, unless ``--always``.
"""

from django.core.management.base import BaseCommand

from apps.bookings.digest import send_digest


class Command(BaseCommand):
    help = "Emails Managers the payment problems that need a person."

    def add_arguments(self, parser):
        parser.add_argument(
            "--always",
            action="store_true",
            help="Send even when nothing needs attention (useful to test email).",
        )

    def handle(self, *args, **options):
        sent_to = send_digest(always=options["always"])
        if sent_to:
            self.stdout.write(self.style.SUCCESS(f"Digest sent to {sent_to} recipient(s)."))
        else:
            self.stdout.write("Nothing needs attention; no digest sent.")
