"""Validate an uploaded image in the isolated OCR Python runtime."""
import io
import sys

try:
    from PIL import Image
except ImportError:
    raise SystemExit(2)

try:
    raw = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('image too large')
    with Image.open(io.BytesIO(raw)) as image:
        if (image.format != sys.argv[1] or image.width * image.height > 20_000_000
                or getattr(image, 'n_frames', 1) != 1):
            raise ValueError('invalid image')
        image.verify()
except Exception:
    raise SystemExit(1)
