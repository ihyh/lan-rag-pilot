"""Administrator-only preview and explicit preparation of staged web images."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response

from .. import web_images
from ..db import get_db
from ..deps import require_kb_admin
from ..ingest import IngestError
from .admin import _raise_ingest

router = APIRouter(prefix='/admin/web-images')
ReferenceId = Annotated[int, Path(ge=1, le=200)]


@router.get('')
def list_images(db=Depends(get_db), user=Depends(require_kb_admin)):
    try:
        return web_images.catalog(db)
    except IngestError as exc:
        _raise_ingest(exc)


@router.get('/{reference_id}/original')
def original_image(reference_id: ReferenceId, user=Depends(require_kb_admin)):
    try:
        item = web_images.item_by_id(reference_id)
        raw = web_images.asset(item)
        return Response(raw, media_type=item['image_info']['mime'],
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except IngestError as exc:
        _raise_ingest(exc)
