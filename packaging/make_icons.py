# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive RevisionWorx's application icons from the Director-supplied logo (docs/REVISIONWORX_BRANDING_DIRECTIVE.md).

    python packaging/make_icons.py            # needs Pillow (a developer tool; nothing at run time needs it)

Input: ``assets/branding/revisionworx-logo-original.png``, exactly as supplied: the blue rounded tile with the white
R, presented on a dark background with a drop shadow. Output:

- ``assets/branding/revisionworx-icon.png``: the tile alone at full resolution, transparent outside it;
- ``revisionworx/gui/resources/revisionworx-{16,24,32,48,64,128,256,512}.png``: the window, taskbar, About and
  splash images;
- ``revisionworx/gui/resources/revisionworx.ico``: the Windows executable icon (16-256 px).

Nothing is redrawn. The tile is found by its blue channel (the dark background never exceeds 60, the tile never
falls below 156 at its rim). Transparency at the edge comes from the artwork's own anti-aliasing (a ramp from blue
60 to 150, limited to a 3-pixel band around the tile), so the original corner shape is kept exactly. The background
colour is then removed from those edge pixels so no dark fringe shows on light backgrounds. The shadow and the
dark background are left out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT / "assets" / "branding" / "revisionworx-logo-original.png"
MASTER = ROOT / "assets" / "branding" / "revisionworx-icon.png"
RESOURCES = ROOT / "revisionworx" / "gui" / "resources"
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

INSIDE_BLUE = 110          # the tile's edge contour (half-way between background and rim)
RAMP = (60.0, 150.0)       # blue values of fully transparent and fully opaque edge pixels
BAND = 3                   # pixels around the tile in which partial transparency is allowed


def _shift_any(mask: np.ndarray, r: int) -> np.ndarray:
    """Dilate a boolean mask by ``r`` pixels (square neighbourhood) without SciPy."""
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def extract_tile(original: Image.Image) -> Image.Image:
    rgb = np.asarray(original.convert("RGB")).astype(np.float64)
    blue = rgb[:, :, 2]
    inside = blue >= INSIDE_BLUE
    ys, xs = np.nonzero(inside)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    side = max(bottom - top, right - left) + 1 + 2 * BAND
    cy, cx = (top + bottom) / 2, (left + right) / 2
    y0, x0 = int(round(cy - side / 2)), int(round(cx - side / 2))
    crop = rgb[y0:y0 + side, x0:x0 + side]
    b = crop[:, :, 2]
    core = crop[:, :, 2] >= INSIDE_BLUE
    # holes cannot exist in the tile, but fill them anyway: anything not reachable from the border is inside
    outside = np.zeros_like(core)
    outside[0, :] = outside[-1, :] = outside[:, 0] = outside[:, -1] = True
    outside &= ~core
    while True:
        grown = _shift_any(outside, 1) & ~core
        if (grown == outside).all():
            break
        outside = grown
    solid = ~outside                          # the tile, letter included
    near = _shift_any(solid, BAND)            # within BAND pixels of the tile
    deep = ~_shift_any(~solid, 2)             # at least 2 pixels inside it
    ramp = np.clip((b - RAMP[0]) / (RAMP[1] - RAMP[0]), 0.0, 1.0)
    alpha = np.zeros(b.shape)
    alpha[near] = ramp[near]                  # the edge keeps the artwork's own anti-aliasing
    alpha[deep] = 1.0
    # remove the background from partially covered pixels: p = a*F + (1-a)*bg  ->  F = (p - (1-a)*bg) / a
    ring = (alpha < 1.0) & (alpha > 0.0)
    far = ~_shift_any(solid, BAND + 6) & _shift_any(solid, BAND + 12)
    bg = np.median(crop[far], axis=0)
    fg = crop.copy()
    a3 = alpha[..., None]
    with np.errstate(divide="ignore", invalid="ignore"):
        est = (crop - (1.0 - a3) * bg) / np.where(a3 > 0, a3, 1.0)
    fg[ring] = np.clip(est[ring], 0, 255)
    # Faint pixels carry too little colour to recover: give them the colour of the nearest well-covered pixel,
    # growing outward one ring at a time from the pixels whose colour is reliable.
    known = alpha >= 0.6
    for _ in range(BAND + 3):
        todo = (alpha > 0) & ~known
        if not todo.any():
            break
        acc = np.zeros_like(fg)
        cnt = np.zeros(alpha.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                s = np.roll(np.roll(known, dy, 0), dx, 1)
                acc += np.roll(np.roll(fg, dy, 0), dx, 1) * s[..., None]
                cnt += s
        fix = todo & (cnt > 0)
        fg[fix] = acc[fix] / cnt[fix][..., None]
        known = known | fix
    # Transparent pixels get the edge colour too ("alpha bleeding"), so a renderer that scales without
    # premultiplied alpha cannot pull the dark presentation background in as a halo.
    known = alpha > 0
    for _ in range(8):
        todo = ~known
        acc = np.zeros_like(fg)
        cnt = np.zeros(alpha.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                s = np.roll(np.roll(known, dy, 0), dx, 1)
                acc += np.roll(np.roll(fg, dy, 0), dx, 1) * s[..., None]
                cnt += s
        fix = todo & (cnt > 0)
        fg[fix] = acc[fix] / cnt[fix][..., None]
        known = known | fix
    fg[~known] = np.median(fg[alpha >= 1.0], axis=0)
    rgba =np.dstack([np.clip(fg, 0, 255), np.clip(alpha * 255.0, 0, 255)]).round().astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def main() -> int:
    master = extract_tile(Image.open(ORIGINAL))
    MASTER.parent.mkdir(parents=True, exist_ok=True)
    master.save(MASTER, optimize=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    for size in PNG_SIZES:
        master.resize((size, size), Image.Resampling.LANCZOS).save(RESOURCES / f"revisionworx-{size}.png", optimize=True)
    big = master.resize((256, 256), Image.Resampling.LANCZOS)
    big.save(RESOURCES / "revisionworx.ico", sizes=[(s, s) for s in ICO_SIZES])
    print(f"master {master.size[0]} px -> {MASTER.relative_to(ROOT)}; "
          f"{len(PNG_SIZES)} PNGs and revisionworx.ico -> {RESOURCES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
