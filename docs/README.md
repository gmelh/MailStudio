# The Mail Studio Book

The owner guide, as a PDF: [The-Mail-Studio-Book.pdf](The-Mail-Studio-Book.pdf).

Source: `book/book.html` (with screenshots in `book/img/`). To rebuild the PDF (needs Python with playwright, pdfplumber and pypdf):

```sh
cd docs/book && python render.py && mv The-Mail-Studio-Book.pdf ..
```
