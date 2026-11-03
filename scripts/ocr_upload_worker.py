"""Bounded local PDF/PNG/JPEG OCR worker. No database, index or generation access."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--pages', default='')
    args = parser.parse_args()
    if args.probe:
        required = ('rapidocr','onnxruntime','cv2','pypdfium2','PIL','pypdf')
        missing = [name for name in required if importlib.util.find_spec(name) is None]
        print(json.dumps({'available':not missing,'missing':missing}))
        return 0
    if args.source is None or args.output is None:
        parser.error('source and output required')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ocr_preprocess as ocr
    is_image=args.source.suffix.lower() in {'.png','.jpg','.jpeg'}
    if args.source.stat().st_size > (10 if is_image else 20) * 1024 * 1024:
        raise ValueError('OCR upload size limit exceeded')
    if is_image:
        from PIL import Image
        with Image.open(args.source) as image:
            expected='PNG' if args.source.suffix.lower()=='.png' else 'JPEG'
            if image.format!=expected or image.width*image.height>20_000_000 or getattr(image,'n_frames',1)!=1:
                raise ValueError('Image must be single-frame PNG/JPEG within 20 million pixels')
            image.verify()
        count=1
    else:
        if args.source.suffix.lower()!='.pdf':
            raise ValueError('OCR upload requires PDF/PNG/JPEG')
        from pypdf import PdfReader
        with args.source.open('rb') as stream:
            count = len(PdfReader(stream).pages)
    if not 1 <= count <= 50:
        raise ValueError('OCR upload limit: 1-50 pages')
    options = ocr.OcrOptions(max_pages=50, force_mixed_ocr=True)
    if args.pages:
        options.pages = ocr.parse_page_selection(args.pages, count)
    result = ocr.process_document(args.source, options, engine=ocr.build_engine('rapidocr'))
    if 'error' in result:
        raise ValueError(result['error']['code'])
    # The standalone processor labels blank OCR as suspect; an empty page cannot
    # be human-approved into this upload index, so expose it as a retryable failure.
    for page in result['pages']:
        if page['status'] in ('ok','suspect') and not page.get('text','').strip():
            page['status_before_upload_validation']=page['status']
            page['status']='failed'
            page['error']={'code':'empty_text','message':'本页未识别出正文，不能入库'}
    result.pop('summary',None)
    if sum(len(page.get('text','')) for page in result['pages']) > 4_000_000:
        raise ValueError('OCR text limit exceeded')
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
