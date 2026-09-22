"""Checking and re-encoding the photos staff upload.

An uploaded file is untrusted input even when a member of staff chose it, so
nothing is stored as it arrived:

- Only JPEG, PNG and WebP are accepted, judged by what Pillow finds in the
  file, not by its name. SVG is refused outright because it can carry script.
- Size and pixel count are capped before the image is decoded, so a small
  file that expands into an enormous bitmap cannot exhaust memory.
- Every accepted image is decoded and written out again as a fresh JPEG. That
  drops EXIF and other metadata (phone photos carry GPS coordinates), and it
  means the bytes we serve are ones Pillow produced rather than ones a browser
  might be talked into reading as something else.
- The stored name is random, so it gives nothing away and cannot collide.
"""

from __future__ import annotations

import io
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}
ACCEPTED_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]

# Forty megapixels is a large camera photo. Anything bigger is refused before
# Pillow decodes it.
MAX_PIXELS = 40_000_000
MIN_WIDTH = 640
MIN_HEIGHT = 400

# Longest edge of each stored rendition.
FULL_SIZE = 2000
CARD_SIZE = 800

JPEG_QUALITY = 82

# Every stored photo is a JPEG this module wrote under a random name, so these
# are the only media paths ever served, whatever else ends up on the disk.
SERVABLE_NAME = re.compile(r"(trips|site)/(cards/)?[0-9a-f]{32}\.jpg")


def _max_bytes() -> int:
    return settings.IMAGE_UPLOAD_MAX_BYTES


def _inspect(file) -> tuple[str, int, int]:
    """Returns (format, width, height) without decoding the pixel data."""
    file.seek(0)
    try:
        with Image.open(file) as image:
            fmt, (width, height) = image.format, image.size
            if width * height > MAX_PIXELS:
                raise ValidationError(
                    "That image is too large to process (%(w)s × %(h)s pixels). "
                    "Export it at a smaller size and try again.",
                    params={"w": width, "h": height},
                    code="too_many_pixels",
                )
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValidationError(
            "That file isn't an image we can read. Upload a JPEG, PNG or WebP photo.",
            code="unreadable",
        ) from exc
    finally:
        file.seek(0)
    return fmt, width, height


def validate_image_upload(value) -> None:
    """Model-field validator for a newly uploaded photo.

    A file that is already stored was checked when it arrived, so only fresh
    uploads are inspected; re-saving a trip does not re-read every image.
    """
    if not value or getattr(value, "_committed", True):
        return
    name = (getattr(value, "name", "") or "").lower()
    if not any(name.endswith(f".{ext}") for ext in ACCEPTED_EXTENSIONS):
        raise ValidationError(
            "Upload a JPEG, PNG or WebP photo.",
            code="bad_extension",
        )
    size = getattr(value, "size", None)
    if size is not None and size > _max_bytes():
        raise ValidationError(
            "That photo is %(mb).1f MB. The limit is %(limit)d MB.",
            params={"mb": size / 1_048_576, "limit": _max_bytes() // 1_048_576},
            code="too_big",
        )
    fmt, width, height = _inspect(value)
    if fmt not in ACCEPTED_FORMATS:
        raise ValidationError(
            "Upload a JPEG, PNG or WebP photo.",
            code="bad_format",
        )
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise ValidationError(
            "That photo is only %(w)s × %(h)s pixels, so it would look blurry. "
            "Use one at least %(min_w)s × %(min_h)s.",
            params={"w": width, "h": height, "min_w": MIN_WIDTH, "min_h": MIN_HEIGHT},
            code="too_small",
        )


def render_jpeg(file, longest_edge: int) -> ContentFile:
    """Decodes ``file`` and returns a clean JPEG no larger than ``longest_edge``.

    Also used on files that never went through a form (a management command,
    the shell), so it applies the same format and pixel limits itself.
    """
    fmt, _, _ = _inspect(file)
    if fmt not in ACCEPTED_FORMATS:
        raise ValidationError("Upload a JPEG, PNG or WebP photo.", code="bad_format")
    file.seek(0)
    with Image.open(file) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        image.thumbnail((longest_edge, longest_edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        # No exif= argument: the new file carries no metadata at all.
        image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    file.seek(0)
    return ContentFile(out.getvalue(), name=f"{uuid.uuid4().hex}.jpg")
