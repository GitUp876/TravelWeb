from django.apps import AppConfig
from django.db.models.signals import post_delete


def _delete_image_files(sender, instance, **kwargs):
    """Removes a deleted row's photos, including rows removed by a cascade."""
    from .models import ProcessedImagesMixin

    if isinstance(instance, ProcessedImagesMixin):
        instance.delete_image_files(instance._image_names())


class CoreConfig(AppConfig):
    default = True
    name = "apps.core"
    verbose_name = "Core"

    def ready(self) -> None:
        post_delete.connect(_delete_image_files, dispatch_uid="core.delete_image_files")
