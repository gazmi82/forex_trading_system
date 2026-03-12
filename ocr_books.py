# =============================================================================
# ocr_books.py — Batch OCR for image-based PDFs
# Processes in 20-page batches at dpi=150 to avoid memory issues.
# Run: python ocr_books.py
# =============================================================================

import os
import gc
import pytesseract
from pdf2image import convert_from_path

BOOKS = [
    {
        "pdf":  "documents/books/Al-Brooks.pdf",
        "out":  "documents/books/al_brooks_ocr.txt",
        "name": "Al Brooks — Trading Price Action"
    },
    {
        "pdf":  "documents/books/the new market wizzards.pdf",
        "out":  "documents/books/new_market_wizards_ocr.txt",
        "name": "The New Market Wizards — Jack Schwager"
    },
    {
        "pdf":  "documents/books/incerto-5-book-bundle-fooled-by-randomness-the-black-swan-the-bed-of-procrustes-antifragile-skin-in-the-game-nassim-nicholas-taleb-1748-pages_compress.pdf",
        "out":  "documents/books/taleb_incerto_ocr.txt",
        "name": "Incerto 5-Book Bundle — Nassim Nicholas Taleb"
    },
]

BATCH_SIZE = 20
DPI        = 150


def ocr_pdf(pdf_path: str, out_path: str, book_name: str):
    import pypdf
    total_pages = len(pypdf.PdfReader(pdf_path).pages)
    print(f"\n{'='*60}")
    print(f"OCR: {book_name}")
    print(f"     {total_pages} pages  →  {out_path}")
    print(f"{'='*60}")

    # Resume from where we left off if output file exists
    start_page = 0
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        marker_count = content.count("=== PAGE ")
        if marker_count > 0:
            start_page = marker_count
            print(f"  Resuming from page {start_page + 1} ({marker_count} pages already done)")

    if start_page >= total_pages:
        print(f"  Already complete — {total_pages} pages done.")
        return

    mode = "a" if start_page > 0 else "w"
    with open(out_path, mode, encoding="utf-8") as f:
        if start_page == 0:
            f.write(f"{book_name}\n")
            f.write("=" * 60 + "\n\n")

        batch_start = start_page
        while batch_start < total_pages:
            batch_end = min(batch_start + BATCH_SIZE, total_pages)
            print(f"  Pages {batch_start + 1}–{batch_end} / {total_pages}...", end=" ", flush=True)

            try:
                images = convert_from_path(
                    pdf_path,
                    dpi=DPI,
                    first_page=batch_start + 1,
                    last_page=batch_end
                )
                for i, img in enumerate(images):
                    page_num = batch_start + i + 1
                    text = pytesseract.image_to_string(img, config="--psm 6")
                    f.write(f"\n=== PAGE {page_num} ===\n")
                    f.write(text)

                del images
                gc.collect()
                print("done")

            except Exception as e:
                print(f"ERROR: {e}")
                print(f"  Skipping batch {batch_start + 1}–{batch_end}")

            batch_start = batch_end

    print(f"\n  ✅ Done → {out_path}")


if __name__ == "__main__":
    print("BATCH OCR — 3 Trading Books")
    print(f"Batch size: {BATCH_SIZE} pages | DPI: {DPI}")

    for book in BOOKS:
        if os.path.exists(book["out"]):
            import pypdf
            total = len(pypdf.PdfReader(book["pdf"]).pages)
            with open(book["out"], "r", encoding="utf-8") as f:
                done = f.read().count("=== PAGE ")
            if done >= total:
                print(f"\n✅ Already complete: {book['name']} ({done} pages)")
                continue

        ocr_pdf(book["pdf"], book["out"], book["name"])

    print("\n" + "="*60)
    print("ALL OCR COMPLETE")
    print("Now run: python main.py --mode ingest")
    print("="*60)