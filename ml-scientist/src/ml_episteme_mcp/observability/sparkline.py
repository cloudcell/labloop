"""Inline SVG sparkline generator — no charting library."""

from __future__ import annotations

import html as html_module


def sparkline(
    values: list[float],
    width: int = 120,
    height: int = 30,
    color: str = "currentColor",
) -> str:
    """Generate an inline SVG sparkline from a list of values.

    Args:
        values: The metric values to plot.
        width: SVG width in pixels.
        height: SVG height in pixels.
        color: Stroke color (CSS color or 'currentColor').

    Returns:
        An SVG string. Empty string if no values.
    """
    if not values:
        return ""

    if len(values) == 1:
        # Single point — render a dot
        cx = width / 2
        cy = height / 2
        return (
            f'<svg class="sparkline" width="{width}" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="{cx}" cy="{cy}" r="2" fill="{color}"/></svg>'
        )

    min_v = min(values)
    max_v = max(values)
    range_v = max_v - min_v if max_v != min_v else 1.0

    points = []
    for i, v in enumerate(values):
        x = i / (len(values) - 1) * width
        y = height - (v - min_v) / range_v * height
        points.append(f"{x:.1f},{y:.1f}")

    points_str = " ".join(points)
    escaped = html_module.escape(points_str)

    return (
        f'<svg class="sparkline" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{escaped}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linejoin="round"/>'
        f"</svg>"
    )
