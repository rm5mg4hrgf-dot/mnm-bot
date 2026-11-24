"""Geometry diagram rendering with Pillow (optional dependency).

If Pillow is not installed, :func:`render_diagram` returns ``None`` and the bot
simply shows the problem without a picture.
"""
from __future__ import annotations

import io
import math
from typing import Any, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

W, H, PAD = 520, 380, 56
BG, LINE, TEXT, ACCENT, FILL = "#171a21", "#dfe6ee", "#ffffff", "#8b5cf6", "#2a2145"


def available() -> bool:
    return Image is not None


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _fit(points: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    scale = min((W - 2 * PAD) / max(maxx - minx, 1e-9), (H - 2 * PAD) / max(maxy - miny, 1e-9))
    ox = (W - (maxx - minx) * scale) / 2
    oy = (H - (maxy - miny) * scale) / 2
    # flip y so that larger y is drawn higher on screen
    return {k: (ox + (x - minx) * scale, H - (oy + (y - miny) * scale)) for k, (x, y) in points.items()}


def _label(draw, xy, text, font, anchor="mm", fill=TEXT):
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _polygon(draw, spec: dict[str, Any], font) -> None:
    pts = _fit(spec["points"])
    order = spec["polygon"]
    corners = [pts[k] for k in order]
    draw.polygon(corners, fill=FILL)
    draw.line(corners + [corners[0]], fill=LINE, width=3, joint="curve")
    for a, b in spec.get("extra_lines", []):
        draw.line([pts[a], pts[b]], fill=ACCENT, width=3)
    cx = sum(c[0] for c in corners) / len(corners)
    cy = sum(c[1] for c in corners) / len(corners)
    for name, (x, y) in pts.items():
        if name in order:
            dx, dy = x - cx, y - cy
            norm = math.hypot(dx, dy) or 1.0
            _label(draw, (x + dx / norm * 20, y + dy / norm * 20), name, font)
        else:  # interior helper point (e.g. the incentre): label just above-right
            _label(draw, (x + 14, y - 16), name, font, fill="#ffd166")
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=ACCENT)
    for edge, text in spec.get("edge_labels", {}).items():
        a, b = pts[edge[0]], pts[edge[1]]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dx, dy = mx - cx, my - cy
        norm = math.hypot(dx, dy) or 1.0
        _label(draw, (mx + dx / norm * 18, my + dy / norm * 18), text, font, fill="#ffd166")
    ra = spec.get("right_angle")
    if ra:
        idx = order.index(ra)
        v = pts[ra]
        n1, n2 = pts[order[(idx - 1) % len(order)]], pts[order[(idx + 1) % len(order)]]
        u1 = ((n1[0] - v[0]), (n1[1] - v[1]))
        u2 = ((n2[0] - v[0]), (n2[1] - v[1]))
        l1, l2 = math.hypot(*u1) or 1, math.hypot(*u2) or 1
        s = 16
        p1 = (v[0] + u1[0] / l1 * s, v[1] + u1[1] / l1 * s)
        p2 = (v[0] + u2[0] / l2 * s, v[1] + u2[1] / l2 * s)
        p3 = (p1[0] + u2[0] / l2 * s, p1[1] + u2[1] / l2 * s)
        draw.line([p1, p3, p2], fill=LINE, width=2)
    for vertex, text in spec.get("angle_labels", {}).items():
        x, y = pts[vertex]
        dx, dy = cx - x, cy - y
        norm = math.hypot(dx, dy) or 1.0
        _label(draw, (x + dx / norm * 38, y + dy / norm * 38), text, font, fill="#7ee0d0")
    hf = spec.get("height_from")
    if hf:  # dashed altitude from a vertex to the opposite side (assumed horizontal base A-B)
        others = [k for k in order if k != hf]
        a, b = pts[others[0]], pts[others[1]]
        foot = (pts[hf][0], a[1] if abs(a[1] - b[1]) < 1e-6 else (a[1] + b[1]) / 2)
        steps = 12
        for i in range(0, steps, 2):
            p = (pts[hf][0] + (foot[0] - pts[hf][0]) * i / steps, pts[hf][1] + (foot[1] - pts[hf][1]) * i / steps)
            q = (pts[hf][0] + (foot[0] - pts[hf][0]) * (i + 1) / steps, pts[hf][1] + (foot[1] - pts[hf][1]) * (i + 1) / steps)
            draw.line([p, q], fill=ACCENT, width=2)
        _label(draw, ((pts[hf][0] + foot[0]) / 2 + 22, (pts[hf][1] + foot[1]) / 2), "h", font, fill="#ffd166")


def _circle(draw, spec: dict[str, Any], font) -> None:
    r_px = 130
    cx, cy = W / 2, H / 2
    draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], outline=LINE, width=3, fill=FILL)
    ratio = spec["d"] / spec["r"]
    y = cy - ratio * r_px
    half = math.sqrt(max(r_px ** 2 - (cy - y) ** 2, 0))
    draw.line([(cx - half, y), (cx + half, y)], fill="#ffd166", width=3)
    draw.line([(cx, cy), (cx, y)], fill=ACCENT, width=2)
    draw.line([(cx, cy), (cx + r_px * 0.7071, cy + r_px * 0.7071)], fill=LINE, width=2)
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=ACCENT)
    labels = spec.get("labels", {})
    _label(draw, (cx - 14, cy + 14), "O", font)
    _label(draw, (cx + 28, (cy + y) / 2), labels.get("distance", ""), font, fill="#7ee0d0")
    _label(draw, (cx + r_px * 0.45 + 22, cy + r_px * 0.45 - 16), labels.get("radius", ""), font)


def render_diagram(spec: Optional[dict[str, Any]]) -> Optional[bytes]:
    """Render a diagram spec to PNG bytes, or None if unavailable/unsupported."""
    if not spec or Image is None:
        return None
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    font = _font(22)
    kind = spec.get("type")
    if kind == "polygon":
        _polygon(draw, spec, font)
    elif kind == "circle":
        _circle(draw, spec, font)
    else:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
