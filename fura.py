import io
from typing import IO

from PIL import Image, ImageDraw, ImageFont

FURA_TEMPLATE = "fura_template.png"
FURA_TEMPLATE_OFFSET = (100, 200)
FURA_TEMPLATE_SIZE = (250, 50)


def get_max_font(image_draw, font_name, text, max_size):
    size = 1
    while True:
        fnt = ImageFont.truetype(font_name, size=size)
        _, _, width, height = image_draw.textbbox((0, 0), text, fnt)
        if width > max_size[0] or height > max_size[1]:
            break
        size += 1

    # Ensure size is a nonnegative integer
    if size > 0:
        size -= 1

    return ImageFont.truetype(font_name, size=size)


def create_fura_image(text: str) -> IO[bytes]:
    img = Image.open(FURA_TEMPLATE)
    d = ImageDraw.Draw(img)
    fnt = get_max_font(d, "DejaVuSans.ttf", text, FURA_TEMPLATE_SIZE)
    _, _, *size = d.textbbox((0, 0), text, fnt)
    offset = [
        template_offset + (template_size - text_size) // 2
        for text_size, template_size, template_offset in zip(
            size, FURA_TEMPLATE_SIZE, FURA_TEMPLATE_OFFSET
        )
    ]
    d.text(offset, text, font=fnt, fill=(0, 0, 0))

    f = io.BytesIO()
    img.save(f, format="png")
    f.seek(0)
    return f


if __name__ == "__main__":
    from argparse import ArgumentParser
    from shutil import copyfileobj

    parser = ArgumentParser()
    parser.add_argument("text")
    args = parser.parse_args()

    with create_fura_image(args.text) as f:
        with open("fura_output.png", "wb") as output_f:
            copyfileobj(f, output_f)
