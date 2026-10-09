# -*- coding: utf-8 -*-
"""이미지 전용 문서의 OCR 보강 헬퍼(SRP: 이미지 판별·수집·변환)."""
import re
from pathlib import Path


def is_image_only_markdown(text: str) -> bool:
    stripped = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text or '')
    stripped = re.sub(r'(?i)\[포맷:[^\]]*\]', '', stripped)
    stripped = re.sub(r'\s+', '', stripped)
    return len(stripped) < 40
def _collect_extract_images(base: Path):
    images = []
    for folder in (base / 'images', base):
        if not folder.exists():
            continue
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}:
                images.append(img)
    return images
def _image_to_ocr_png(src: Path, dest_dir: Path) -> Path:
    """kordoc 이미지 OCR은 PNG/JPG/WebP만 받으므로 BMP 등은 PNG로 변환한다."""
    ext = src.suffix.lower()
    if ext in {'.png', '.jpg', '.jpeg', '.webp'}:
        return src
    dest = dest_dir / (src.stem + '.png')
    from PIL import Image  # pyright: ignore[reportMissingImports]  # 선택 의존성: 변환이 필요할 때만 쓴다
    Image.open(src).convert('RGB').save(dest)
    return dest
