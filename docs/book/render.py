"""Render book.html to a PDF: contents with page numbers, page numbers in the footer, plain cover."""

import re
from pathlib import Path

import pdfplumber
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

HERE = Path(__file__).parent
SOURCE = (HERE / "book.html").read_text()

FOOTER = """<div style="width:100%;font:8pt Inter,sans-serif;color:#8792a6;padding:0 20mm;display:flex;justify-content:space-between">
<span>The Mail Studio Book</span><span class="pageNumber"></span></div>"""


def entries(html: str) -> list[tuple[str, str, str]]:
    """(kind, title, marker text to find on the page) in document order."""
    found = []
    for m in re.finditer(r'data-toc(-part)?="([^"]+)"[^>]*>\s*(?:<header>\s*)?<div class="kicker">([^<]+)</div>', html):
        kind = "part" if m.group(1) else "chapter"
        found.append((kind, m.group(2), m.group(3).strip().upper()))
    return found


def toc_html(items: list[tuple[str, str, str]], pages: dict[str, int]) -> str:
    out, number = [], 0
    for kind, title, marker in items:
        if kind == "part":
            out.append(f'<div class="tpart">{title}</div>')
            continue
        label = ""
        if marker.startswith("CHAPTER"):
            number += 1
            label = str(number)
        elif marker.startswith("APPENDIX"):
            if not any("Appendices" in o for o in out):
                out.append('<div class="tpart">Appendices</div>')
            label, title = title.split(" · ", 1)
        out.append(
            f'<div class="row"><span class="n">{label}</span><span>{title}</span>'
            f'<span class="dots"></span><span class="pg">{pages.get(marker, "")}</span></div>'
        )
    return "\n".join(out)


def render(html: str, path: Path, footer: bool) -> None:
    (HERE / "_render.html").write_text(html)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto((HERE / "_render.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.pdf(
            path=str(path),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=footer,
            header_template="<div></div>",
            footer_template=FOOTER if footer else "<div></div>",
        )
        browser.close()


def find_pages(pdf: Path, items: list[tuple[str, str, str]]) -> dict[str, int]:
    pages: dict[str, int] = {}
    with pdfplumber.open(pdf) as doc:
        texts = [(page.extract_text() or "") for page in doc.pages]
    start = 2  # skip the cover and contents
    for _kind, _title, marker in items:
        for index in range(start, len(texts)):
            if marker in texts[index].splitlines()[:3]:
                pages[marker] = index + 1
                start = index
                break
    return pages


def main() -> None:
    items = entries(SOURCE)
    draft = HERE / "_draft.pdf"
    render(SOURCE.replace("{{TOC}}", toc_html(items, {m: 88 for *_, m in items})), draft, footer=True)
    pages = find_pages(draft, items)
    missing = [m for *_, m in items if m not in pages]
    assert not missing, f"not found: {missing}"

    body = HERE / "_body.pdf"
    render(SOURCE.replace("{{TOC}}", toc_html(items, pages)), body, footer=True)
    cover_only = re.sub(r"<!-- =+ -->\s*<section class=\"toc\".*</body>", "</body>", SOURCE, flags=re.S)
    cover = HERE / "_cover.pdf"
    render(cover_only, cover, footer=False)

    writer = PdfWriter()
    writer.add_page(PdfReader(cover).pages[0])
    for page in PdfReader(body).pages[1:]:
        writer.add_page(page)
    writer.add_metadata({"/Title": "The Mail Studio Book", "/Author": "Mail Studio"})
    out = HERE / "The-Mail-Studio-Book.pdf"
    with out.open("wb") as f:
        writer.write(f)
    print(out, len(writer.pages), "pages", pages)


if __name__ == "__main__":
    main()
