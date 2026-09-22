"""Charges every payment-plan instalment that has come due.

Run from the platform's scheduler, once or twice a day. Each instalment is
charged off-session against the card the guest saved at their deposit checkout.
The charge is idempotent per instalment, so running the command twice, or
having it overlap with a previous run, never takes a payment twice.

A declined instalment marks itself and its plan as needing attention rather
than retrying blindly, so staff can chase the guest for a new card.
"""

from django.core.management.base import BaseCommand

from apps.payments.plans import charge_due_instalments


class Command(BaseCommand):
    help = "Charges payment-plan instalments that are due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the instalments that would be charged without charging them.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            from apps.payments.plans import due_instalments

            due = list(due_instalments())
            for sp in due:
                self.stdout.write(
                    f"{sp.plan.booking.reference}: ${sp.amount} due {sp.due_date:%d %b %Y}"
                )
            self.stdout.write(self.style.WARNING(f"{len(due)} instalment(s) due (dry run)."))
            return

        charged, failed = charge_due_instalments()
        self.stdout.write(
            self.style.SUCCESS(f"Charged {charged} instalment(s); {failed} need attention.")
        )
