"""Registers a TOTP device for a staff account.

The admin refuses anyone without a verified device, so a new account is set up
with:

    python manage.py setup_mfa someone@example.com

which prints an otpauth:// URI to load into an authenticator app. The secret is
printed once and never stored anywhere else.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Creates a TOTP device for a staff account and prints its enrolment URI."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="authenticator")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Remove existing devices for this account first.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(email__iexact=options["email"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No account for {options['email']}") from exc

        if not user.is_staff:
            raise CommandError("Only staff accounts need an authenticator")

        existing = TOTPDevice.objects.filter(user=user)
        if existing.exists() and not options["replace"]:
            raise CommandError("This account already has a device; pass --replace to reissue")
        if options["replace"]:
            existing.delete()

        device = TOTPDevice.objects.create(user=user, name=options["name"], confirmed=True)
        self.stdout.write(self.style.SUCCESS(f"Device created for {user.email}"))
        self.stdout.write("Scan this in an authenticator app, then delete this output:")
        self.stdout.write(device.config_url)
