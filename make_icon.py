"""Draws easyrecord.ico (and icon.png). Run: python make_icon.py  (needs Pillow)."""
from PIL import Image, ImageDraw

BG_TOP, BG_BOTTOM = (58, 66, 82), (30, 34, 42)
RED, RED_HI = (229, 57, 53), (255, 112, 104)


def draw(size):
    big = 1024 if size >= 48 else 512
    s = big
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Dark rounded tile with a soft top-to-bottom shade.
    shade = Image.new("RGBA", (s, s))
    sd = ImageDraw.Draw(shade)
    for y in range(s):
        t = y / s
        sd.line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * t)
                                             for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (s, s), 0)
    pad = s * 0.04
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, s - pad, s - pad],
                                           radius=s * 0.22, fill=255)
    img.paste(shade, (0, 0), mask)
    d = ImageDraw.Draw(img)

    if size >= 32:
        # White screen frame with corner brackets, like a capture box.
        m, w, L = s * 0.2, s * 0.055, s * 0.17
        for (x, y, dx, dy) in [(m, m, 1, 1), (s - m, m, -1, 1),
                               (m, s - m, 1, -1), (s - m, s - m, -1, -1)]:
            d.line([(x, y + dy * w / 2), (x + dx * L, y + dy * w / 2)], fill="white", width=int(w))
            d.line([(x + dx * w / 2, y), (x + dx * w / 2, y + dy * L)], fill="white", width=int(w))
        r = s * 0.2
    else:
        r = s * 0.3   # tiny sizes: just a big dot so it stays readable

    c = s / 2
    d.ellipse([c - r, c - r, c + r, c + r], fill=RED)
    hr = r * 0.35   # small highlight
    d.ellipse([c - r * 0.45 - hr / 2, c - r * 0.45 - hr / 2,
               c - r * 0.45 + hr / 2, c - r * 0.45 + hr / 2], fill=RED_HI)
    return img.resize((size, size), Image.LANCZOS)


sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
frames = [draw(n) for n in sizes]
frames[-1].save("easyrecord.ico", sizes=[(n, n) for n in sizes],
                append_images=frames[:-1])
draw(512).save("icon.png")
print("wrote easyrecord.ico")
