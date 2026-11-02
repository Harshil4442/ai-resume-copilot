"""Small real synthetic PDF bytes for owned-source tests; no worker receipt."""
from io import BytesIO

from reportlab.pdfgen.canvas import Canvas


def source_for_text(text: str) -> bytes:
    output = BytesIO()
    canvas = Canvas(output, invariant=True)
    canvas.setFont("Helvetica", 11)
    for index, line in enumerate(text.splitlines()):
        canvas.drawString(48, 740 - index * 16, line)
    canvas.save()
    return output.getvalue()
