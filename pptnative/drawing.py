"""Read-only native source catalog and shape traversal."""

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE



EMU_PER_PT = 12700
RENDER_VERSION = 3


def walk(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from walk(shape.shapes)


def catalog(template):
    deck = Presentation(template)
    scale = deck.slide_width / 1280
    pages = []
    for i, slide in enumerate(deck.slides, 1):
        slots = []
        for shape in walk(slide.shapes):
            if shape.has_text_frame and shape.text.strip():
                runs = [run for p in shape.text_frame.paragraphs for run in p.runs]
                size = next((run.font.size.pt for run in runs if run.font.size), 16)
                face = next((run.font.name for run in runs if run.font.name and not run.font.name.startswith("+")),
                            "Microsoft YaHei")
                slots.append({"id": shape.shape_id, "text": shape.text[:140],
                              "box": [round(value / scale, 1) for value in
                                      (shape.left, shape.top, shape.width, shape.height)],
                              "font_px": round(size * EMU_PER_PT / scale, 1), "font_face": face})
        pages.append({"number": i, "slots": slots,
                      "pictures": sum(s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in walk(slide.shapes))})
    return {"canvas": [1280, round(deck.slide_height / scale, 2)], "slides": pages}
