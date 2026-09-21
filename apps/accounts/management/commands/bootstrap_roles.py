from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.roles import ROLES


class Command(BaseCommand):
    help = "Creates or updates the Staff and Manager permission groups."

    @transaction.atomic
    def handle(self, *args, **options):
        for role, entries in ROLES.items():
            group, created = Group.objects.get_or_create(name=role)
            wanted = set()
            for app_label, model, actions in entries:
                for action in actions:
                    codename = f"{action}_{model}"
                    perm = Permission.objects.filter(
                        content_type__app_label=app_label, codename=codename
                    ).first()
                    if perm is None:
                        self.stderr.write(f"missing permission {app_label}.{codename}")
                        continue
                    wanted.add(perm)
            group.permissions.set(wanted)
            verb = "created" if created else "updated"
            self.stdout.write(
                self.style.SUCCESS(f"{verb} group {role} ({len(wanted)} permissions)")
            )
