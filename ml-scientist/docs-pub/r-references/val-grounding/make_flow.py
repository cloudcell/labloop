#!/usr/bin/env python3
"""Flowchart: how every scoring value in the loop is derived, and where
the derivation stops.

Rendered with matplotlib patches (no TikZ in the lab image).
Line spacing is derived from the real data-units-per-point ratio so that
stacked text does not collide.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from matplotlib.lines import Line2D

UNS_E, UNS_F = "#b03a2e", "#fbeae8"
SCO_E, SCO_F = "#1f4e79", "#e8f0f8"
EMI_E, EMI_F = "#4a4a4a", "#f1f1f1"
OP_E,  OP_F  = "#1a1a1a", "#e4e4e4"
DEA_E, DEA_F = "#8f8f8f", "#f7f7f7"
GAT_E, GAT_F = "#1e6b34", "#e9f5ec"
GOL_E, GOL_F = "#8a6d00", "#fdf6e0"

W, H = 100.0, 152.0
FIG_W, FIG_H = 7.1, 8.1
DPU = FIG_H * 72.0 / H          # data units per point


def step(fs, lead=1.30):
    return fs * lead / DPU


fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")


def panel(y, h, tag, caption, tint):
    ax.add_patch(Rectangle((1.5, y), W - 3, h, linewidth=0,
                           facecolor=tint, alpha=0.55, zorder=0))
    ax.text(2.6, y + h - 1.2, tag, ha="left", va="top", fontsize=7.0,
            fontweight="bold", color="#444", zorder=7)
    ax.text(7.4, y + h - 1.2, caption, ha="left", va="top",
            fontsize=7.0, color="#666", zorder=7)


def bxl(x, y, w, h, ec, fc, ls="solid", r=1.3, z=3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
        linewidth=0.9, edgecolor=ec, facecolor=fc, linestyle=ls, zorder=z))


def stack(x, y, w, h, lines, fs, cy=None, weight="normal", mono=False,
          color="#111"):
    """Centre a stack of lines in the rect; cy overrides vertical centre."""
    s = step(fs)
    base = y + h / 2 if cy is None else cy
    top = base + s * (len(lines) - 1) / 2
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, top - i * s, ln, ha="center", va="center",
                fontsize=fs, color=color, fontweight=weight,
                family="monospace" if mono else "sans-serif", zorder=6)


def arrow(x1, y1, x2, y2, color, ls="-", lw=1.0, ms=7):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=ms,
        linewidth=lw, color=color, linestyle=ls, zorder=5,
        shrinkA=0, shrinkB=0))


# =====================================================================  A
panel(130, 20, "A", "SOURCE \u2014 hardcoded literals. No derivation, no citation, no config path.", "#fdecea")
CONST = [
    ("0.85", ["VERDICT_CONFIDENCE", "episteme/tools/", "programme.py:38"]),
    ("0.6", ["arete/tools/", "decisions.py:122", "every meta-decision"]),
    ("0.3", ["PRIOR_CONFIDENCE_MAX", "anamnesis + zetesis", "the only sourced rule"]),
    ("0.5 / 1", ["claim.importance", "graph edge weight", "strong = weak evidence"]),
    ("0.0", ["null fallback", "zetesis/tools/", "promotion.py:1158"]),
]
bw, gap = 17.0, 1.6
x0 = (W - (len(CONST) * bw + (len(CONST) - 1) * gap)) / 2
for i, (val, sub) in enumerate(CONST):
    x = x0 + i * (bw + gap)
    bxl(x, 131.0, bw, 15.0, UNS_E, UNS_F)
    ax.text(x + bw / 2, 142.9, val, ha="center", va="center", fontsize=12,
            color=UNS_E, fontweight="bold", family="monospace", zorder=6)
    stack(x, 131.0, bw, 15.0, sub, 5.5, cy=135.6, color="#6a4a42")

# =====================================================================  B
panel(96, 32, "B", "SCORING LAYER \u2014 pure arithmetic. The only data entering is the recorded metric value.", "#eef4fa")

bxl(3, 110, 45, 10.5, SCO_E, SCO_F)
stack(3, 110, 45, 10.5,
      ["recursive_gain = E[best|cand] / E[best|par]",
       "                 inverted if direction = min"], 5.8, mono=True)

bxl(52, 110, 45, 10.5, SCO_E, SCO_F)
stack(52, 110, 45, 10.5,
      ["promotion_score = mean(chal) / mean(champ)",
       "                 no direction, no n recorded"], 5.2, mono=True)

bxl(3, 101.5, 21, 6.4, GAT_E, GAT_F)
stack(3, 101.5, 21, 6.4, ["divisor = 0", "REFUSES the close"],
      5.2, weight="bold", color=GAT_E)

bxl(27, 101.5, 21, 6.4, UNS_E, UNS_F)
stack(27, 101.5, 21, 6.4, ["divisor = 0", "freezes score = 0.0"],
      5.2, weight="bold", color=UNS_E)

bxl(52, 101.5, 45, 6.4, GOL_E, GOL_F)
stack(52, 101.5, 45, 6.4, ["verdict \u2208 { accepted, rejected, inconclusive }"],
      5.2, mono=True, color=GOL_E)

ax.text(50, 98.4, "challenger 0.5 beats champion 0.0 \u2014 an undefined ratio "
                  "\u2014 and the frozen score reads as total failure",
        ha="center", va="center", fontsize=6.0, style="italic",
        color=UNS_E, zorder=6)

# =====================================================================  C
panel(58, 38, "C", "EMITTED VALUES \u2014 the numbers that actually reach the operator", "#f0f0f0")
EMIT = [
    ["claim.confidence", "= lookup[verdict string]", "no data input at all"],
    ["claim.importance", "= 0.5  always", "no data input at all"],
    ["edge.weight = 1", "strong and weak", "evidence identical"],
]
bw2, gap2 = 30.0, 2.5
x0b = (W - (3 * bw2 + 2 * gap2)) / 2
for i, sub in enumerate(EMIT):
    x = x0b + i * (bw2 + gap2)
    bxl(x, 64, bw2, 14, EMI_E, EMI_F)
    stack(x, 64, bw2, 14, sub, 6.0, mono=True)
    ax.text(x + bw2 / 2, 61.4, "\u2715  no n, no dispersion",
            ha="center", va="center", fontsize=5.5, color=UNS_E, zorder=6)

ax.text(50, 87.5, "belief.param_importance = 1 / n_vars  (uniform) whenever n < 2",
        ha="center", va="center", fontsize=5.8, color=DEA_E, zorder=6)
ax.text(50, 59.4, "every value above is a float in the same schema; the interface "
                  "does not distinguish measured from asserted",
        ha="center", va="center", fontsize=5.8, style="italic",
        color="#777", zorder=6)

# =====================================================================  D
panel(38, 18, "D", "OPERATOR-VISIBLE SURFACE", "#e8e8e8")
bxl(3, 40, 94, 11.5, OP_E, OP_F)
stack(3, 40, 94, 11.5,
      ["status digest \u00b7 web dashboard \u00b7 claim graph \u00b7 MCP read surface",
       "confidence 0.85  |  recursive_gain 1.00  |  promotion_score 0  |  importance 0.5"],
      5.6, weight="bold", mono=True)

# =====================================================================  E
panel(11, 25, "E", "RECORDED BUT NEVER CONSUMED \u2014 the inputs a score of this kind would need", "#f5f5f5")
DEAD = [
    ["seed count  n", "stored on the tournament,", "recorded nowhere near", "the frozen score"],
    ["dispersion", "variance IS recorded per", "observation, but reaches", "neither scorer"],
    ["effect size", "no SESOI, no minimum", "detectable effect, no", "power calculation"],
    ["n = 1 allowed", "close requires \u2265 1", "result per arm \u2014 and", "never more than that"],
]
bw3, gap3 = 21.5, 2.2
x0c = (W - (4 * bw3 + 3 * gap3)) / 2
for i, sub in enumerate(DEAD):
    x = x0c + i * (bw3 + gap3)
    bxl(x, 13, bw3, 13, DEA_E, DEA_F, ls=(0, (4, 2)))
    ax.text(x + bw3 / 2, 23.0, sub[0], ha="center", va="center", fontsize=6.6,
            fontweight="bold", color="#5f5f5f", zorder=6)
    stack(x, 13, bw3, 13, sub[1:], 5.5, cy=17.6, color="#8a8a8a")
    arrow(x + bw3 / 2, 26, x + bw3 / 2, 33.0, DEA_E, ls=(0, (3, 2)))
    ax.text(x + bw3 / 2 + 1.0, 29.5, "\u2715", ha="left", va="center",
            fontsize=8.5, color=UNS_E, fontweight="bold", zorder=6)

# ---------------------------------------------------------------- arrows
for i in range(len(CONST)):
    x = x0 + i * (bw + gap) + bw / 2
    tgt = 25.5 if i < 2 else 74.5
    arrow(x, 131.0, tgt, 120.5, UNS_E, ls=(0, (3, 2)), lw=0.85, ms=6)

arrow(25.5, 110, 25.5, 78, SCO_E, lw=1.1)
arrow(74.5, 110, 60.0, 78, SCO_E, lw=1.1)
arrow(74.5, 101.5, 40.0, 78, GOL_E, lw=1.1)
ax.text(78.0, 93.0, "the only input to claim.confidence", ha="left",
        va="center", fontsize=5.6, style="italic", color=GOL_E, zorder=6)

for i in range(3):
    x = x0b + i * (bw2 + gap2) + bw2 / 2
    arrow(x, 64, x, 51.5, EMI_E, lw=1.1)

# ---------------------------------------------------------------- gates
bxl(3, 1.4, 94, 7.4, GAT_E, GAT_F)
ax.text(50, 6.4, "STRUCTURAL GATES THAT DO WORK \u2014 no numbers involved",
        ha="center", va="center", fontsize=6.8, fontweight="bold",
        color=GAT_E, zorder=6)
ax.text(50, 3.3, "evidence edge required to exceed 0.3   \u00b7   contract frozen at open   \u00b7   one budget for both arms   \u00b7   conclusions insert-only",
        ha="center", va="center", fontsize=5.1, color=GAT_E, zorder=6)

# ---------------------------------------------------------------- legend
ax.legend(handles=[
    Line2D([], [], color=UNS_E, lw=1.3, ls=(0, (3, 2)),
           label="hardcoded / unsourced"),
    Line2D([], [], color=SCO_E, lw=1.3, label="scoring function"),
    Line2D([], [], color=GOL_E, lw=1.3, label="categorical input"),
    Line2D([], [], color=DEA_E, lw=1.3, ls=(0, (4, 2)),
           label="recorded, never consumed"),
], loc="upper center", bbox_to_anchor=(0.5, -0.004), fontsize=6.0,
   frameon=True, framealpha=0.96, ncol=4, handlelength=2.0,
   columnspacing=1.6, borderpad=0.55)

fig.savefig("flow.pdf", format="pdf", bbox_inches="tight", pad_inches=0.05)
fig.savefig("flow.png", format="png", dpi=200, bbox_inches="tight", pad_inches=0.05)
print("ok")
