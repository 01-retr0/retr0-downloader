"""Generates icon.ico and icon.png (pixel-art R0 on a dark tile). Needs Pillow."""
from PIL import Image, ImageDraw

G = {
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
}
GRAD = ["#00ff9c", "#00f0c8", "#00e5ff", "#00b8ff", "#7a5cff", "#ff2d6f", "#ff2d6f"]
N = 256
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, N - 8, N - 8), radius=44, fill="#07070d", outline="#00ff9c", width=6)
cell = 17
cols = 11
x0 = (N - cols * cell) // 2
y0 = (N - 7 * cell) // 2
for ci, ch in enumerate("R0"):
    for r, row in enumerate(G[ch]):
        for c, bit in enumerate(row):
            if bit == "#":
                x = x0 + (ci * 6 + c) * cell
                y = y0 + r * cell
                d.rectangle((x, y, x + cell - 2, y + cell - 2), fill=GRAD[r])
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save("icon.png")
print("icon.ico and icon.png written")
