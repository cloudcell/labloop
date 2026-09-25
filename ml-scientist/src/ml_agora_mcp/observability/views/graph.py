"""Cross-server entity timeline — /graph + /graph/data.

Fetches each loop's ``X://graph`` resource through the read-only
channels and merges into one node/edge set. Rendered as a vertical
timeline — earliest records at the top — with one swim lane per
loop (claims | loop0 | loop1 | loop2), drawn by the vendored
PixiJS WebGL build. Double-click a node to open the owning server's
GUI page in the detail panel.

This plugin is intentionally self-contained: retiring it removes the
two routes, the menu entry, and nothing else. No state, no writes —
a projection over live channel reads.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ..links import ENTITY_PREFIXES, LEVELS, gui_base
from ..templates import escape, render_base

ROUTE = "/graph"
NAV_TITLE = "Graph"

# channel name -> (loop level, graph resource URI)
_CHANNELS = {
    "claims": (0, "claims://graph"),
    "loop0": (1, "protocol://graph"),
    "loop1": (2, "search://graph"),
    "loop2": (3, "improver://graph"),
}

_COLORS = {
    0: "#f59e0b",  # claims — amber
    1: "#2dd4bf",  # loop0 — teal
    2: "#60a5fa",  # loop1 — blue
    3: "#c084fc",  # loop2 — violet
}


async def _fetch_graph(adaptors, name: str, uri: str) -> dict | None:
    spec = adaptors._channels.get(name)
    live = getattr(adaptors, name, None)
    dead = getattr(live, "session_dead", None)
    if spec is None or live is None or (dead and dead()):
        return None
    try:
        text = await live.read_resource(uri)
        return json.loads(text) if isinstance(text, str) else text
    except Exception:
        return None


async def merged_graph(adaptors) -> dict:
    """Fetch + merge all four server graphs into one payload."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    sources: dict[str, str] = {}
    gui_bases: dict[str, str | None] = {}

    for name, (level, uri) in _CHANNELS.items():
        spec = adaptors._channels.get(name)
        gui_bases[name] = gui_base(spec.target) if spec else None
        payload = await _fetch_graph(adaptors, name, uri)
        if payload is None:
            sources[name] = "unreachable"
            continue
        sources[name] = "ok" + (
            "+truncated" if payload.get("truncated") else ""
        )
        for n in payload.get("nodes", []):
            nid = n["id"]
            node = dict(n)
            node["level"] = level
            node["loop"] = payload.get("loop", name)
            gp = node.pop("gui_path", None)
            if gp and gui_bases.get(name):
                node["gui_url"] = gui_bases[name] + gp
            # Prefer richer nodes (real over stubs) on id collision.
            if nid not in nodes or "status" in node:
                nodes[nid] = node
        edges.extend(payload.get("edges", []))

    # Stub out edge endpoints that no server claimed — dedupe across
    # loops (a prog-* referenced by a zetesis spawn AND present in
    # loop0's graph is one node).
    for e in edges:
        for key in ("src", "dst"):
            nid = e[key]
            if nid in nodes:
                continue
            hint = next(
                (v for p, v in ENTITY_PREFIXES.items()
                 if nid.startswith(p)),
                None,
            )
            chan, path, kind = hint or (None, None, "entity")
            level = LEVELS.get(chan, 1)
            node = {"id": nid, "kind": kind, "label": nid,
                    "level": level, "loop": chan or "unknown",
                    "stub": True}
            if path and chan and gui_bases.get(chan):
                node["gui_url"] = gui_bases[chan] + path + nid
            nodes[nid] = node

    return {
        "generated_at": _utc_now(),
        "sources": sources,
        "nodes": list(nodes.values()),
        "edges": [
            e for e in edges
            if e.get("src") in nodes and e.get("dst") in nodes
        ],
        "levels": {"claims": 0, "loop0": 1, "loop1": 2, "loop2": 3},
        "colors": _COLORS,
    }


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_PAGE = """
<h1>Entity timeline</h1>
<p class="muted">Swim lanes per loop — claims | loop0 | loop1 |
loop2 — earliest records at the top. Scroll to zoom, drag to pan;
double-click a node to open it in the detail panel.
<span id="graph-sources"></span></p>
<div id="graph-layout">
    <div id="graph-side">
        <div id="mini-holder"></div>
        <div id="zoom-ctl">
            <a href="#" id="zin" title="Zoom in">+</a>
            <a href="#" id="zout" title="Zoom out">−</a>
            <a href="#" id="zfit" title="Fit">⤢</a>
        </div>
    </div>
    <div id="graph-canvas"></div>
</div>
<div id="graph-tip" class="muted"></div>
<div id="detail-panel">
    <div id="detail-head">
        <span id="detail-title" class="muted"></span>
        <a id="detail-close" href="#" title="Close">✕</a>
    </div>
    <iframe id="detail-frame" title="detail"></iframe>
</div>
<script src="/static/pixi.min.js"></script>
<script>
fetch('/graph/data').then(r => r.json()).then(function(data) {
    var src = document.getElementById('graph-sources');
    src.textContent = ' sources: ' + Object.entries(data.sources)
        .map(e => e[0] + '=' + e[1]).join(', ')
        + ' · ' + data.nodes.length + ' nodes';

    // ---- layout: lanes across x, time down y ------------------
    var LANES = ['claims', 'loop0', 'loop1', 'loop2'];
    var LANE_W = 300, NODE_SEP = 26, TOP = 60;
    var dated = data.nodes.filter(function(n) { return n.ts; });
    var t0 = Math.min.apply(null, dated.map(function(n) {
        return Date.parse(n.ts); }));
    var t1 = Math.max.apply(null, dated.map(function(n) {
        return Date.parse(n.ts); }));
    if (!(t0 < t1)) { t1 = t0 + 1; }
    var SPAN_PX = Math.max(4000, dated.length * 8);

    var pos = {};   // id -> {x, y}
    LANES.forEach(function(lane, li) {
        var cx = 90 + li * LANE_W;
        var row = data.nodes.filter(function(n) {
            return n.level === li; });
        var prev = TOP;
        row.filter(function(n) { return n.ts; })
           .sort(function(a, b) {
               return Date.parse(a.ts) - Date.parse(b.ts); })
           .forEach(function(n) {
               var y = TOP +
                   ((Date.parse(n.ts) - t0) / (t1 - t0)) * SPAN_PX;
               if (y < prev + NODE_SEP) { y = prev + NODE_SEP; }
               prev = y;
               pos[n.id] = { x: cx, y: y };
           });
        // Undated stubs park in a small grid at the lane's top.
        row.filter(function(n) { return !n.ts; })
           .forEach(function(n, i) {
               pos[n.id] = {
                   x: cx - 90 + (i % 6) * 36,
                   y: TOP - 44 + Math.floor(i / 6) * 24
               };
           });
    });
    var worldW = 90 + (LANES.length - 1) * LANE_W + 150;
    var worldH = TOP + SPAN_PX + 120;
    // Same-second nodes push past SPAN_PX — the world must reach
    // the deepest node, not just the last timestamp.
    var laneCount = [0, 0, 0, 0];
    data.nodes.forEach(function(n) {
        var p = pos[n.id];
        if (!p) { return; }
        laneCount[n.level] += 1;
        if (p.y + 80 > worldH) { worldH = p.y + 80; }
    });
    var maxLaneItems = Math.max.apply(null, laneCount);

    // ---- pixi scene --------------------------------------------
    var holder = document.getElementById('graph-canvas');
    var app = new PIXI.Application({
        resizeTo: holder,
        backgroundColor: 0x0b0f14,
        antialias: true,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true
    });
    holder.appendChild(app.view);
    var world = new PIXI.Container();
    app.stage.addChild(world);

    // Lane separators + headers.
    var chrome = new PIXI.Graphics();
    LANES.forEach(function(lane, li) {
        var cx = 90 + li * LANE_W;
        chrome.lineStyle(1, 0x1f2937)
              .moveTo(cx, 0).lineTo(cx, worldH);
    });
    world.addChild(chrome);
    LANES.forEach(function(lane, li) {
        var h = new PIXI.Text(lane, {
            fill: 0x94a3b8, fontSize: 13, fontFamily: 'ui-monospace'
        });
        h.x = 90 + li * LANE_W - h.width / 2;
        h.y = 14;
        world.addChild(h);
    });
    // Sparse time ticks along the left edge.
    for (var i = 0; i <= 10; i++) {
        var ty = TOP + (i / 10) * SPAN_PX;
        var tick = new PIXI.Text(
            new Date(t0 + (i / 10) * (t1 - t0))
                .toISOString().slice(5, 16).replace('T', ' '),
            { fill: 0x475569, fontSize: 10,
              fontFamily: 'ui-monospace' });
        tick.x = 0; tick.y = ty;
        world.addChild(tick);
    }

    // Edges first so nodes draw over them.
    var edgeG = new PIXI.Graphics();
    edgeG.lineStyle(1, 0x334155, 0.55);
    data.edges.forEach(function(e) {
        var a = pos[e.src], b = pos[e.dst];
        if (a && b) { edgeG.moveTo(a.x, a.y).lineTo(b.x, b.y); }
    });
    world.addChild(edgeG);

    // Nodes — rounded boxes, colored per loop, dimmed when closed.
    var tip = document.getElementById('graph-tip');
    var hitNodes = {};
    data.nodes.forEach(function(n) {
        var p = pos[n.id];
        if (!p) { return; }
        var c = parseInt(data.colors[n.level].slice(1), 16);
        var dim = (n.status === 'closed' || n.status === 'archived'
            || n.status === 'superseded');
        var box = new PIXI.Graphics();
        if (n.stub) {
            box.beginFill(0x475569).drawCircle(0, 0, 4).endFill();
        } else {
            box.lineStyle(1, c)
               .beginFill(c, 0.22)
               .drawRoundedRect(-58, -10, 116, 20, 5)
               .endFill();
        }
        box.x = p.x; box.y = p.y;
        box.alpha = dim ? 0.45 : 1;
        box.eventMode = 'static';
        box.cursor = n.gui_url ? 'pointer' : 'default';
        var meta = n;
        box.on('pointerover', function() {
            tip.textContent = n.kind + ' · ' + (n.status || '—')
                + ' · ' + n.id
                + (n.ts ? ' · ' + n.ts.slice(0, 19) + 'Z' : '');
            box.alpha = 1;
        });
        box.on('pointerout', function() {
            tip.textContent = '';
            box.alpha = dim ? 0.45 : 1;
        });
        if (n.gui_url) {
            box.on('click', function() {
                var now = Date.now();
                if (meta._lastClick && now - meta._lastClick < 350) {
                    openDetail(meta);
                }
                meta._lastClick = now;
            });
        }
        world.addChild(box);
        if (!n.stub) {
            var lbl = new PIXI.Text(
                (n.label || n.id).slice(0, 18), {
                    fill: 0xe2e8f0, fontSize: 9,
                    fontFamily: 'ui-monospace' });
            lbl.anchor.set(0.5);
            lbl.eventMode = 'none';
            lbl.x = p.x; lbl.y = p.y;
            lbl.alpha = dim ? 0.45 : 1;
            world.addChild(lbl);
        }
        hitNodes[n.id] = box;
    });

    // ---- detail panel (double-click = hyperlink) ---------------
    var panel = document.getElementById('detail-panel');
    var frame = document.getElementById('detail-frame');
    var dtitle = document.getElementById('detail-title');
    function openDetail(n) {
        dtitle.textContent = n.id;
        frame.src = n.gui_url;
        panel.classList.add('open');
    }
    document.getElementById('detail-close').onclick = function(ev) {
        ev.preventDefault();
        frame.src = 'about:blank';
        panel.classList.remove('open');
    };
    // Native dblclick on the canvas — hit-test in world space.
    // More reliable than synthesizing a double-click from Pixi
    // 'click' events, which the stage's drag handlers can swallow.
    holder.addEventListener('dblclick', function(e) {
        var r = holder.getBoundingClientRect();
        var wx = (e.clientX - r.left - world.x) / world.scale.x;
        var wy = (e.clientY - r.top - world.y) / world.scale.y;
        var best = null, bd = Infinity;
        data.nodes.forEach(function(n) {
            var p = pos[n.id];
            if (!p || !n.gui_url) { return; }
            var hw = n.stub ? 10 : 62, hh = n.stub ? 10 : 14;
            var dx = Math.abs(p.x - wx), dy = Math.abs(p.y - wy);
            if (dx < hw && dy < hh) {
                var d = dx * dx + dy * dy;
                if (d < bd) { bd = d; best = n; }
            }
        });
        if (best) { openDetail(best); }
    });

    // ---- pan + zoom ---------------------------------------------
    var drag = null;
    app.stage.eventMode = 'static';
    app.stage.hitArea = app.screen;
    app.stage.on('pointerdown', function(e) {
        drag = { x: e.global.x, y: e.global.y,
                 wx: world.x, wy: world.y };
    });
    app.stage.on('pointerup', function() { drag = null; });
    app.stage.on('pointerupoutside', function() { drag = null; });
    app.stage.on('pointermove', function(e) {
        if (!drag) { return; }
        world.x = drag.wx + (e.global.x - drag.x);
        world.y = drag.wy + (e.global.y - drag.y);
    });
    holder.addEventListener('wheel', function(e) {
        e.preventDefault();
        var r = holder.getBoundingClientRect();
        zoomAt(e.clientX - r.left, e.clientY - r.top,
               e.deltaY < 0 ? 1.12 : 0.89);
    }, { passive: false });

    // Fit world width to the canvas on load.
    var fit = Math.min(1, holder.clientWidth / worldW);
    world.scale.set(fit);
    world.y = 10;

    // ---- zoom around a screen point ----------------------------
    function zoomAt(px, py, k) {
        var s = Math.min(4, Math.max(0.05, world.scale.x * k));
        var r = s / world.scale.x;
        world.x = px - (px - world.x) * r;
        world.y = py - (py - world.y) * r;
        world.scale.set(s);
    }
    function zoomCenter(k) {
        zoomAt(holder.clientWidth / 2, holder.clientHeight / 2, k);
    }
    function fitAll() {
        var s = Math.min(holder.clientWidth / worldW,
                         holder.clientHeight / worldH);
        world.scale.set(s);
        world.x = (holder.clientWidth - worldW * s) / 2;
        world.y = 10;
    }
    document.getElementById('zin').onclick = function(e) {
        e.preventDefault(); zoomCenter(1.4); };
    document.getElementById('zout').onclick = function(e) {
        e.preventDefault(); zoomCenter(0.72); };
    document.getElementById('zfit').onclick = function(e) {
        e.preventDefault(); fitAll(); };

    // ---- minimap: separate renderer in the left pane -----------
    // X fits the pane width; Y scales independently so every item
    // gets its own row (MINI_ROW px) — the pane scrolls over
    // whatever is taller than it.
    var miniHolder = document.getElementById('mini-holder');
    var MINI_ROW = 4;
    function miniScales() {
        var sx = miniHolder.clientWidth / worldW;
        var sy = Math.max(sx, maxLaneItems * MINI_ROW / worldH);
        return { sx: sx, sy: sy };
    }
    var ms = miniScales();
    var miniApp = new PIXI.Application({
        width: miniHolder.clientWidth,
        height: Math.ceil(worldH * ms.sy),
        backgroundColor: 0x0f172a,
        antialias: false,
        resolution: window.devicePixelRatio || 1,
        autoDensity: true
    });
    miniApp.view.style.display = 'block';
    miniHolder.appendChild(miniApp.view);
    var miniStage = new PIXI.Container();
    miniApp.stage.addChild(miniStage);
    var mini = new PIXI.Graphics();
    LANES.forEach(function(lane, li) {
        var cx = 90 + li * LANE_W;
        mini.lineStyle(1, 0x1f2937, 0.8)
            .moveTo(cx, 0).lineTo(cx, worldH);
    });
    data.nodes.forEach(function(n) {
        var p = pos[n.id];
        if (!p) { return; }
        var c = n.stub ? 0x475569
            : parseInt(data.colors[n.level].slice(1), 16);
        mini.beginFill(c, n.stub ? 0.4 : 0.85)
            .drawRect(p.x - 8, p.y - 8, 16, 16)
            .endFill();
    });
    miniStage.addChild(mini);

    var view = new PIXI.Graphics();   // viewport rectangle
    miniStage.addChild(view);
    function fitMini() {
        var ms = miniScales();
        miniStage.scale.set(ms.sx, ms.sy);
        miniStage.x = (miniHolder.clientWidth - worldW * ms.sx) / 2;
        miniApp.renderer.resize(
            miniHolder.clientWidth, Math.ceil(worldH * ms.sy));
    }
    fitMini();
    window.addEventListener('resize', fitMini);

    // Click/drag on the map centers the main view on that point.
    var miniDrag = false;
    function jumpTo(e) {
        var lp = e.getLocalPosition(miniStage);
        var s = world.scale.x;
        world.x = holder.clientWidth / 2 - lp.x * s;
        world.y = holder.clientHeight / 2 - lp.y * s;
    }
    mini.eventMode = 'static';
    mini.hitArea = new PIXI.Rectangle(0, 0, worldW, worldH);
    mini.cursor = 'crosshair';
    mini.on('pointerdown', function(e) { miniDrag = true; jumpTo(e); });
    mini.on('globalpointermove', function(e) {
        if (miniDrag) jumpTo(e); });
    mini.on('pointerup', function() { miniDrag = false; });
    mini.on('pointerupoutside', function() { miniDrag = false; });

    // The viewport rectangle is grab-draggable (4-arrow cursor):
    // moving it pans the world by the same world-space delta.
    var viewDrag = null;
    view.eventMode = 'static';
    view.cursor = 'move';
    view.on('pointerdown', function(e) {
        e.stopPropagation();
        var lp = e.getLocalPosition(miniStage);
        viewDrag = { x: lp.x, y: lp.y, wx: world.x, wy: world.y };
    });
    view.on('globalpointermove', function(e) {
        if (!viewDrag) { return; }
        var lp = e.getLocalPosition(miniStage);
        var s = world.scale.x;
        world.x = viewDrag.wx - (lp.x - viewDrag.x) * s;
        world.y = viewDrag.wy - (lp.y - viewDrag.y) * s;
    });
    view.on('pointerup', function() { viewDrag = null; });
    view.on('pointerupoutside', function() { viewDrag = null; });

    // Keep the viewport rectangle in sync — cheap once per frame.
    app.ticker.add(function() {
        app.stage.hitArea = app.screen;
        var s = world.scale.x;
        view.clear()
            // transparent fill makes the interior hit-testable —
            // needed for the move cursor + grab-drag.
            .beginFill(0xffffff, 0.001)
            .drawRect(-world.x / s, -world.y / s,
                      holder.clientWidth / s, holder.clientHeight / s)
            .endFill()
            .lineStyle(8, 0x94a3b8, 0.9)
            .drawRect(-world.x / s, -world.y / s,
                      holder.clientWidth / s, holder.clientHeight / s);
    });
});
</script>
"""


def routes(ctx) -> list[Route]:
    """Plugin contract — the graph page + its data endpoint."""

    def page(request: Request) -> HTMLResponse:
        return HTMLResponse(render_base("Entity graph", _PAGE,
                                        active=ROUTE, wide=True))

    async def data(request: Request) -> JSONResponse:
        return JSONResponse(await merged_graph(ctx.adaptors))

    return [
        Route(ROUTE, page),
        Route("/graph/data", data),
    ]
