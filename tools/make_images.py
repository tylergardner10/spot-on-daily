"""Draws the link-preview card and icons for Spot On Daily as pixel art (no Pillow needed).
Usage: python tools/make_images.py game"""
import os, random, struct, sys, zlib

NAVY, PANEL, EDGE = (14, 16, 48), (27, 30, 74), (143, 151, 255)
YELLOW, RED, WHITE, MUTED = (255, 210, 63), (232, 51, 79), (244, 245, 255), (168, 173, 224)
TURF_A, TURF_B, LINE = (47, 158, 79), (39, 144, 69), (236, 240, 255)
CROWD = [(232, 51, 79), (255, 210, 63), (61, 220, 132), (143, 151, 255), (244, 245, 255), (255, 140, 66), (90, 200, 250)]

FONT = {  # 5x7
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00000", "00100"],
    " ": ["00000"] * 7,
}


class Canvas:
    def __init__(self, w, h, color):
        self.w, self.h = w, h
        self.px = [[color] * w for _ in range(h)]

    def rect(self, x, y, w, h, c):
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.px[yy][xx] = c

    def text(self, s, x, y, scale, c, shadow=None):
        for i, ch in enumerate(s):
            for r, row in enumerate(FONT[ch]):
                for col, bit in enumerate(row):
                    if bit == "1":
                        gx, gy = x + i * 6 * scale + col * scale, y + r * scale
                        if shadow:
                            self.rect(gx + scale // 2 + 1, gy + scale // 2 + 1, scale, scale, shadow)
                        self.rect(gx, gy, scale, scale, c)

    def text_width(self, s, scale):
        return (len(s) * 6 - 1) * scale

    def png(self, path, upscale):
        raw = b""
        for row in self.px:
            line = b"".join(bytes(c) * upscale for c in row)
            raw += (b"\x00" + line) * upscale
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        ihdr = struct.pack(">IIBBBBB", self.w * upscale, self.h * upscale, 8, 2, 0, 0, 0)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def og(path):
    """1200x630 link preview: crowd, scoreboard panel with the logo, turf."""
    c = Canvas(240, 126, NAVY)
    rnd = random.Random(15)
    for y in range(0, 100, 3):  # crowd
        for x in range(0, 240, 3):
            if rnd.random() < 0.8:
                c.rect(x, y, 2, 2, rnd.choice(CROWD))
    for y in range(98, 126):  # turf with yard lines
        c.rect(0, y, 240, 1, TURF_A if (y // 4) % 2 else TURF_B)
    for x in range(10, 240, 22):
        c.rect(x, 98, 1, 28, LINE)
    c.rect(0, 97, 240, 1, LINE)
    # scoreboard panel
    c.rect(36, 14, 172, 88, (5, 6, 26))       # block shadow
    c.rect(32, 10, 172, 88, EDGE)
    c.rect(34, 12, 168, 84, PANEL)
    t1, t2, t3, t4 = "SPOT ON", "DAILY", "GUESS THE STAT.", "SPOTONDAILY.COM"
    c.text(t1, (240 - c.text_width(t1, 3)) // 2, 18, 3, YELLOW, RED)
    c.text(t2, (240 - c.text_width(t2, 2)) // 2, 46, 2, WHITE)
    c.rect(60, 64, 120, 1, EDGE)
    c.text(t3, (240 - c.text_width(t3, 1)) // 2, 70, 1, YELLOW)
    c.text(t4, (240 - c.text_width(t4, 1)) // 2, 82, 1, MUTED)
    c.png(path, 5)


def icon(path, size_px):
    """Square app icon: yellow S on the navy scoreboard above a turf strip (18x18 grid)."""
    c = Canvas(18, 18, NAVY)
    c.rect(0, 15, 18, 3, TURF_A)
    c.rect(0, 15, 18, 1, LINE)
    c.text("S", 4, 1, 2, YELLOW)
    c.png(path, size_px // 18)


if __name__ == "__main__":
    out = sys.argv[1]
    og(os.path.join(out, "og.png"))
    icon(os.path.join(out, "favicon.png"), 36)
    icon(os.path.join(out, "apple-touch-icon.png"), 180)
    print("wrote og.png (1200x630), favicon.png (36), apple-touch-icon.png (180)")
