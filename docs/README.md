# The MailStudio Book

The owner guide, as a PDF: [The-MailStudio-Book.pdf](The-MailStudio-Book.pdf).

Source: `book/book.html` (with screenshots in `book/img/`). To rebuild the PDF (needs Python with playwright, pdfplumber and pypdf):

```sh
cd docs/book && python render.py && mv The-MailStudio-Book.pdf ..
```
