#!/usr/bin/env python3
# =============================================================================
# pdf_to_markdown.py — Convert PDFs into cleaned Markdown for RAG ingestion
#
# Examples:
#   python pdf_to_markdown.py --all
#   python pdf_to_markdown.py documents/research --ocr-fallback
#   python pdf_to_markdown.py "documents/research/My Report.pdf" --force
# =============================================================================

from __future__ import annotations

import argparse
import gc
import logging
import re
from datetime import datetime, timezone
from pathlib import Path


logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("pypdf").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)


DEFAULT_DIRS = [
    Path("documents/books"),
    Path("documents/research"),
    Path("documents/ict"),
    Path("documents/cot"),
    Path("documents/journal"),
]


def slug_title(stem: str) -> str:
    text = stem.replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_text(text: str) -> str:
    """Normalize PDF/OCR text into paragraph-oriented Markdown."""
    text = text.replace("\ufeff", "")
    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)

    lines = [line.strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    current: list[str] = []

    def flush():
        if not current:
            return
        paragraph = " ".join(current).strip()
        paragraph = re.sub(r"\s+", " ", paragraph)
        if paragraph:
            paragraphs.append(paragraph)
        current.clear()

    for line in lines:
        if not line:
            flush()
            continue
        if re.fullmatch(r"\d+", line):
            flush()
            continue
        if len(line) == 1:
            continue
        current.append(line)

    flush()
    return "\n\n".join(paragraphs).strip()


def extract_pdf_text(pdf_path: Path) -> tuple[str, int, int]:
    """Return (text, page_count, pages_with_text) using pypdf."""
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    pages: list[str] = []
    pages_with_text = 0

    for page in reader.pages:
        text = page.extract_text() or ""
        text = clean_text(text)
        if text:
            pages_with_text += 1
            pages.append(text)

    return "\n\n".join(pages).strip(), len(reader.pages), pages_with_text


def extract_pdf_text_ocr(pdf_path: Path, dpi: int = 150, batch_size: int = 20) -> tuple[str, int, int]:
    """Return (text, page_count, pages_with_text) using OCR."""
    import pypdf
    import pytesseract
    from pdf2image import convert_from_path

    page_count = len(pypdf.PdfReader(str(pdf_path)).pages)
    pages: list[str] = []
    pages_with_text = 0

    for batch_start in range(0, page_count, batch_size):
        batch_end = min(batch_start + batch_size, page_count)
        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=batch_start + 1,
            last_page=batch_end,
        )

        for image in images:
            text = pytesseract.image_to_string(image, config="--psm 6")
            text = clean_text(text)
            if text:
                pages_with_text += 1
                pages.append(text)

        del images
        gc.collect()

    return "\n\n".join(pages).strip(), page_count, pages_with_text


def build_markdown(pdf_path: Path, body: str, method: str, page_count: int, pages_with_text: int) -> str:
    title = slug_title(pdf_path.stem)
    generated_at = datetime.now(timezone.utc).isoformat()
    safe_title = title.replace('"', "'")
    safe_name = pdf_path.name.replace('"', "'")
    return (
        f"---\n"
        f'title: "{safe_title}"\n'
        f'source_pdf: "{safe_name}"\n'
        f'extraction_method: "{method}"\n'
        f"page_count: {page_count}\n"
        f"pages_with_text: {pages_with_text}\n"
        f'generated_at: "{generated_at}"\n'
        f"---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def convert_pdf(
    pdf_path: Path,
    force: bool = False,
    ocr_fallback: bool = False,
    min_chars: int = 1500,
    dpi: int = 150,
    batch_size: int = 20,
    delete_pdf: bool = False,
) -> dict:
    out_path = pdf_path.with_suffix(".md")
    if out_path.exists() and not force:
        return {"status": "skipped_existing", "pdf": pdf_path, "out": out_path}

    try:
        text, page_count, pages_with_text = extract_pdf_text(pdf_path)
        method = "pypdf"
    except Exception as exc:
        if not ocr_fallback:
            return {"status": "error", "pdf": pdf_path, "error": f"pypdf failed: {exc}"}
        text, page_count, pages_with_text = "", 0, 0
        method = "ocr"

    if len(text) < min_chars and ocr_fallback:
        try:
            ocr_text, ocr_pages, ocr_pages_with_text = extract_pdf_text_ocr(
                pdf_path, dpi=dpi, batch_size=batch_size
            )
            if len(ocr_text) > len(text):
                text = ocr_text
                page_count = ocr_pages
                pages_with_text = ocr_pages_with_text
                method = "ocr"
        except Exception as exc:
            if not text:
                return {"status": "error", "pdf": pdf_path, "error": f"OCR failed: {exc}"}

    if len(text) < 200:
        return {"status": "too_short", "pdf": pdf_path, "chars": len(text)}

    markdown = build_markdown(pdf_path, text, method, page_count, pages_with_text)
    out_path.write_text(markdown, encoding="utf-8")

    if delete_pdf:
        pdf_path.unlink()

    return {
        "status": "converted",
        "pdf": pdf_path,
        "out": out_path,
        "chars": len(text),
        "method": method,
        "pages": page_count,
        "pages_with_text": pages_with_text,
    }


def iter_pdfs(inputs: list[Path], all_dirs: bool) -> list[Path]:
    targets = DEFAULT_DIRS if all_dirs or not inputs else inputs
    pdfs: list[Path] = []

    for target in targets:
        if target.is_file() and target.suffix.lower() == ".pdf":
            pdfs.append(target)
            continue
        if target.is_dir():
            pdfs.extend(sorted(target.rglob("*.pdf")))

    seen: set[Path] = set()
    unique: list[Path] = []
    for pdf in pdfs:
        resolved = pdf.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(pdf)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Convert PDFs into cleaned Markdown for RAG.")
    parser.add_argument("inputs", nargs="*", help="PDF files or directories to convert")
    parser.add_argument("--all", action="store_true", help="Convert PDFs in all documents/* folders")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .md files")
    parser.add_argument("--ocr-fallback", action="store_true", help="Use OCR when text extraction is weak")
    parser.add_argument("--delete-pdf", action="store_true", help="Delete the source PDF after a successful conversion")
    parser.add_argument("--min-chars", type=int, default=1500, help="Minimum text size before OCR fallback")
    parser.add_argument("--dpi", type=int, default=150, help="OCR DPI")
    parser.add_argument("--batch-size", type=int, default=20, help="OCR pages per batch")
    args = parser.parse_args()

    inputs = [Path(p) for p in args.inputs]
    pdfs = iter_pdfs(inputs, args.all)
    if not pdfs:
        print("No PDFs found.")
        return

    print(f"Converting {len(pdfs)} PDF(s) to Markdown...")
    converted = 0
    skipped = 0
    errors = 0

    for pdf in pdfs:
        print(f"\nProcessing: {pdf}")
        result = convert_pdf(
            pdf,
            force=args.force,
            ocr_fallback=args.ocr_fallback,
            min_chars=args.min_chars,
            dpi=args.dpi,
            batch_size=args.batch_size,
            delete_pdf=args.delete_pdf,
        )

        status = result["status"]
        if status == "converted":
            converted += 1
            print(
                f"  ✅ {result['out'].name} | {result['method']} | "
                f"{result['chars']} chars | {result['pages_with_text']}/{result['pages']} pages"
            )
        elif status == "skipped_existing":
            skipped += 1
            print(f"  ⏭️  {result['out'].name} already exists")
        elif status == "too_short":
            skipped += 1
            print(f"  ⚠️  Skipped — extracted text too short ({result['chars']} chars)")
        else:
            errors += 1
            print(f"  ❌ {result['error']}")

    print(
        f"\nDone. Converted: {converted} | Skipped: {skipped} | Errors: {errors}\n"
        "Next step: run `python main.py --mode ingest`"
    )


if __name__ == "__main__":
    main()
