#!/usr/bin/env python3
"""
Generate every RemotePhone icon asset from one source definition.

    pip install cairosvg pillow
    python tools/generate_icons.py

Produces:
  android/app/src/main/res/drawable/ic_launcher_{background,foreground}.xml
  android/app/src/main/res/mipmap-*dpi/ic_launcher{,_round}.png
  android/app/src/main/ic_launcher-playstore.png
  remotephone/assets/icon.svg, icon-*.png, icon.ico, icon.icns
  assets/logo-dark.png, assets/logo-light.png, assets/social-preview.png
"""

from pathlib import Path
import re
import sys

try:
    import cairosvg
except ImportError:
    sys.exit("Missing dependency: pip install cairosvg")
try:
    from PIL import Image
except ImportError:
    sys.exit("Missing dependency: pip install pillow")

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- palette ---
BG_ICON = "#16162B"   # icon tile background
BG_PAGE = "#0D0D0F"   # app window background, used for the social preview
WHITE = "#FFFFFF"
PURPLE = "#7C3AED"
MUTED = "#6B7280"
SCREEN = "#2D2D44"   # phone screen inside the mark
INK = "#1A1A2E"       # wordmark colour on light backgrounds

CANVAS = 512          # all geometry below is authored on a 512x512 grid


def n(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def rrect(x, y, w, h, r):
    if r <= 0:
        return f"M{n(x)},{n(y)} H{n(x + w)} V{n(y + h)} H{n(x)} Z"
    return (
        f"M{n(x + r)},{n(y)} H{n(x + w - r)} A{n(r)},{n(r)} 0 0 1 {n(x + w)},{n(y + r)} "
        f"V{n(y + h - r)} A{n(r)},{n(r)} 0 0 1 {n(x + w - r)},{n(y + h)} "
        f"H{n(x + r)} A{n(r)},{n(r)} 0 0 1 {n(x)},{n(y + h - r)} "
        f"V{n(y + r)} A{n(r)},{n(r)} 0 0 1 {n(x + r)},{n(y)} Z"
    )


def circle(cx, cy, r):
    return (
        f"M{n(cx - r)},{n(cy)} a{n(r)},{n(r)} 0 1 0 {n(2 * r)},0 "
        f"a{n(r)},{n(r)} 0 1 0 {n(-2 * r)},0 Z"
    )


def beam_arc(cx, cy, r, half_deg=55):
    """Right-bulging arc fanning +-half_deg about due east, one beam wave."""
    from math import sin, cos, radians
    dx, dy = r * cos(radians(half_deg)), r * sin(radians(half_deg))
    return (f"M{n(cx + dx)},{n(cy - dy)} "
            f"A{n(r)},{n(r)} 0 0 1 {n(cx + dx)},{n(cy + dy)}")


def mark(scale=1.0, detail=True):
    """
    Direction C - 'Beam': phone broadcasting purple waves to the right.
    A near-square composition (x118-394, y118-394 at scale 1) so it fills
    circular launcher masks instead of sitting as a thin bar inside them.

    scale=1.0 is the reference size; the adaptive foreground uses a smaller
    scale to respect the safe zone, unmasked contexts use a larger one.
    """
    def t(v):        # scale a position about the canvas centre
        return CANVAS / 2 + (v - CANVAS / 2) * scale

    def L(v):        # scale a length
        return v * scale

    shapes = [
        dict(d=rrect(t(118), t(118), L(148), L(276), L(26)), fill=WHITE),
        dict(d=rrect(t(132), t(140), L(120), L(216), L(9)), fill=SCREEN),
    ]
    if detail:
        shapes.append(dict(d=rrect(t(168), t(370), L(48), L(8), L(4)), fill=MUTED))
    cx, cy = t(266), t(256)   # beams radiate from the phone's right edge
    for r in (44, 86, 128):
        shapes.append(dict(d=beam_arc(cx, cy, L(r)), stroke=PURPLE, width=L(20)))
    return shapes


# -------------------------------------------------------------------- svg ---
def svg_shapes(shapes, indent="  "):
    out = []
    for s in shapes:
        if "fill" in s:
            out.append(f'{indent}<path fill="{s["fill"]}" d="{s["d"]}"/>')
        else:
            out.append(
                f'{indent}<path fill="none" stroke="{s["stroke"]}" '
                f'stroke-width="{n(s["width"])}" stroke-linecap="round" d="{s["d"]}"/>'
            )
    return "\n".join(out)


def icon_svg(scale=1.0, shape="rounded", detail=True, bg=BG_ICON):
    """shape: 'rounded' | 'square' | 'circle' | 'none' (transparent)."""
    if shape == "rounded":
        back = f'<path fill="{bg}" d="{rrect(0, 0, CANVAS, CANVAS, CANVAS * 0.22)}"/>'
    elif shape == "square":
        back = f'<rect width="{CANVAS}" height="{CANVAS}" fill="{bg}"/>'
    elif shape == "circle":
        back = f'<path fill="{bg}" d="{circle(CANVAS / 2, CANVAS / 2, CANVAS / 2)}"/>'
    else:
        back = ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}" '
        f'width="{CANVAS}" height="{CANVAS}">\n'
        f'  {back}\n{svg_shapes(mark(scale, detail))}\n</svg>\n'
    )


def png(svg, path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=svg.encode(), write_to=str(path),
        output_width=size, output_height=size,
    )


# ------------------------------------------------------- android vectors ---
VD_SCALE = 108 / CANVAS  # adaptive icons are authored on a 108dp grid


def vector_drawable(shapes, scaled=True):
    body = []
    for s in shapes:
        if "fill" in s:
            body.append(
                f'        <path\n'
                f'            android:fillColor="{s["fill"]}"\n'
                f'            android:pathData="{s["d"]}" />'
            )
        else:
            body.append(
                f'        <path\n'
                f'            android:strokeColor="{s["stroke"]}"\n'
                f'            android:strokeWidth="{n(s["width"])}"\n'
                f'            android:strokeLineCap="round"\n'
                f'            android:fillColor="#00000000"\n'
                f'            android:pathData="{s["d"]}" />'
            )
    inner = "\n".join(body)
    if scaled:
        inner = (
            f'    <group\n'
            f'        android:scaleX="{VD_SCALE}"\n'
            f'        android:scaleY="{VD_SCALE}">\n'
            f'{inner}\n'
            f'    </group>'
        )
    else:
        inner = inner.replace("        <path", "    <path").replace(
            "            android:", "        android:")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated by tools/generate_icons.py - do not edit by hand. -->\n'
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:width="108dp"\n'
        '    android:height="108dp"\n'
        '    android:viewportWidth="108"\n'
        '    android:viewportHeight="108">\n'
        f'{inner}\n'
        '</vector>\n'
    )


# ------------------------------------------------------------------ build ---
def wordmark_paths():
    """Inline the pre-converted (font-free) wordmark outlines."""
    src = (ROOT / "assets" / "wordmark.svg").read_text()
    inner = re.search(r"<g fill=\"currentColor\">(.*)</g>", src, re.S).group(1)
    vb = re.search(r'viewBox="([-\d. ]+)"', src).group(1).split()
    return inner.strip(), [float(v) for v in vb]


def build():
    res = ROOT / "android/app/src/main/res"
    made = []

    # --- Android adaptive icon (API 26+) ---
    bg_shape = [dict(d=rrect(0, 0, 108, 108, 0), fill=BG_ICON)]
    (res / "drawable/ic_launcher_background.xml").write_text(
        vector_drawable(bg_shape, scaled=False))
    (res / "drawable/ic_launcher_foreground.xml").write_text(
        vector_drawable(mark(0.82)))
    made += ["drawable/ic_launcher_background.xml", "drawable/ic_launcher_foreground.xml"]

    # --- Android legacy raster fallback (API 24-25 have no adaptive icons) ---
    square = icon_svg(scale=1.12, shape="rounded")
    round_ = icon_svg(scale=1.12, shape="circle")
    for bucket, size in [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
                         ("xxhdpi", 144), ("xxxhdpi", 192)]:
        png(square, res / f"mipmap-{bucket}/ic_launcher.png", size)
        png(round_, res / f"mipmap-{bucket}/ic_launcher_round.png", size)
        made.append(f"mipmap-{bucket}/ic_launcher{{,_round}}.png ({size}px)")

    # --- Play Store listing icon: 512px, opaque, square ---
    png(icon_svg(scale=1.0, shape="square"),
        ROOT / "android/app/src/main/ic_launcher-playstore.png", 512)
    made.append("ic_launcher-playstore.png (512px)")

    # --- F-Droid listing icon (fastlane) ---
    png(icon_svg(scale=1.0, shape="square"),
        ROOT / "fastlane/metadata/android/en-US/images/icon.png", 512)
    made.append("fastlane images/icon.png (512px)")

    # --- Desktop client ---
    dest = ROOT / "remotephone/assets"
    dest.mkdir(parents=True, exist_ok=True)
    desktop = icon_svg(scale=1.18, shape="rounded")
    small = icon_svg(scale=1.18, shape="rounded", detail=False)  # drops hairlines
    (dest / "icon.svg").write_text(desktop)
    for size in (64, 128, 256, 512):
        png(desktop, dest / f"icon-{size}.png", size)
    made.append("remotephone/assets/icon.svg + icon-{64,128,256,512}.png")

    # .ico: small sizes use the simplified mark
    tmp = ROOT / ".icon-tmp"
    tmp.mkdir(exist_ok=True)
    frames = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        p = tmp / f"{size}.png"
        png(small if size <= 32 else desktop, p, size)
        frames.append(Image.open(p).convert("RGBA"))
    frames[-1].save(dest / "icon.ico", format="ICO",
                    sizes=[(f.width, f.height) for f in frames])
    made.append("remotephone/assets/icon.ico (16-256px)")

    try:
        icns = Image.open(tmp / "256.png").convert("RGBA").resize((1024, 1024), Image.LANCZOS)
        icns.save(dest / "icon.icns", format="ICNS")
        made.append("remotephone/assets/icon.icns")
    except Exception as exc:            # ICNS support is optional in Pillow
        print(f"  note: skipped icon.icns ({exc})")

    for f in frames:
        f.close()
    for p in tmp.glob("*.png"):
        p.unlink()
    tmp.rmdir()

    # --- README logo lockup ---
    marks, vb = wordmark_paths()
    mx, my, mw, mh = vb
    ICON, GAP, PAD = 128, 34, 0
    TEXT_H = 62                                   # cap height of the wordmark
    k = TEXT_H / mh
    tw = mw * k
    W = ICON + GAP + tw
    H = ICON
    ty = (H + TEXT_H) / 2 - (my + mh) * k         # baseline-ish vertical centring

    def logo(colour):
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n(W)} {n(H)}" '
            f'width="{n(W)}" height="{n(H)}">\n'
            f'  <g transform="translate(0,0) scale({n(ICON / CANVAS)})">\n'
            f'    <path fill="{BG_ICON}" d="{rrect(0, 0, CANVAS, CANVAS, CANVAS * 0.22)}"/>\n'
            f'{svg_shapes(mark(1.18), indent="    ")}\n'
            f'  </g>\n'
            f'  <g fill="{colour}" transform="translate({n(ICON + GAP)},{n(ty)}) '
            f'scale({n(k)})">\n    {marks}\n  </g>\n</svg>\n'
        )

    (ROOT / "assets/logo.svg").write_text(logo(WHITE))
    for name, colour in (("logo-dark", WHITE), ("logo-light", INK)):
        cairosvg.svg2png(bytestring=logo(colour).encode(),
                         write_to=str(ROOT / f"assets/{name}.png"),
                         output_width=int(W * 2), output_height=int(H * 2))
    made.append("assets/logo.svg, logo-dark.png, logo-light.png")

    # --- Self-contained badge: the lockup on its own card. PyPI strips
    #     <picture>, so the README's <img> fallback needs one image that is
    #     legible on light and dark pages alike. ---
    PADX, PADY = 56, 44
    BW, BH = W + 2 * PADX, H + 2 * PADY
    badge = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n(BW)} {n(BH)}" '
        f'width="{n(BW)}" height="{n(BH)}">\n'
        f'  <path fill="{BG_PAGE}" d="{rrect(0, 0, BW, BH, 28)}"/>\n'
        f'  <g transform="translate({PADX},{PADY})">\n'
        + logo(WHITE).split("\n", 1)[1].rsplit("</svg>", 1)[0]
        + '  </g>\n</svg>\n'
    )
    cairosvg.svg2png(bytestring=badge.encode(),
                     write_to=str(ROOT / "assets/logo-badge.png"),
                     output_width=int(BW * 2), output_height=int(BH * 2))
    made.append("assets/logo-badge.png")

    # --- GitHub social preview (1280x640) ---
    SW, SH = 1280, 640
    si, sgap = 168, 40
    stext_h = 82
    sk = stext_h / mh
    stw = mw * sk
    block_w = si + sgap + stw
    ox = (SW - block_w) / 2
    oy = 216
    sty = oy + si / 2 + stext_h / 2 - (my + mh) * sk
    tag = "Mirror and control your Android phone over WiFi"
    social = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}" '
        f'width="{SW}" height="{SH}">\n'
        f'  <rect width="{SW}" height="{SH}" fill="{BG_PAGE}"/>\n'
        f'  <rect x="0" y="{SH - 6}" width="{SW}" height="6" fill="{PURPLE}"/>\n'
        f'  <g transform="translate({n(ox)},{oy}) scale({n(si / CANVAS)})">\n'
        f'    <path fill="{BG_ICON}" d="{rrect(0, 0, CANVAS, CANVAS, CANVAS * 0.22)}"/>\n'
        f'{svg_shapes(mark(1.18), indent="    ")}\n'
        f'  </g>\n'
        f'  <g fill="{WHITE}" transform="translate({n(ox + si + sgap)},{n(sty)}) '
        f'scale({n(sk)})">\n    {marks}\n  </g>\n'
        f'  <text x="{SW / 2}" y="{oy + si + 74}" fill="#9CA3AF" text-anchor="middle" '
        f'font-family="Inter, DejaVu Sans, sans-serif" font-size="30">{tag}</text>\n'
        f'</svg>\n'
    )
    cairosvg.svg2png(bytestring=social.encode(),
                     write_to=str(ROOT / "assets/social-preview.png"),
                     output_width=SW, output_height=SH)
    made.append("assets/social-preview.png (1280x640)")

    print("Generated:")
    for m in made:
        print("  -", m)


if __name__ == "__main__":
    build()
