# SPDX-License-Identifier: Apache-2.0
"""Walk a page's content stream and find out what is drawn where.

The result is a list of *items*: runs of text, images, form XObjects, vector
paths and shadings, each with the range of content-stream operators that draws
it, a bounding box in page space, and (for text) the decoded string and font
size. The auto-tagger uses the operator ranges to wrap content in marked
content; the checker and reviewer use the marked-content map (MCID -> box and
text) to describe existing tags.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pikepdf

from .fonts import FontCache, FontInfo

Matrix = tuple[float, float, float, float, float, float]
BBox = tuple[float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

PATH_CONSTRUCT = {"m", "l", "c", "v", "y", "h", "re"}
PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
TEXT_SHOW = {"Tj", "TJ", "'", '"'}


def mul(a: Matrix, b: Matrix) -> Matrix:
    """Matrix product a x b (PDF row-vector convention)."""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


def bbox_of(points: list[tuple[float, float]]) -> BBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def transform_bbox(m: Matrix, box: BBox) -> BBox:
    x0, y0, x1, y1 = box
    return bbox_of([apply(m, x0, y0), apply(m, x1, y0), apply(m, x0, y1), apply(m, x1, y1)])


def union(a: BBox | None, b: BBox | None) -> BBox | None:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _matrix(arr) -> Matrix:
    try:
        vals = [float(v) for v in arr]
        if len(vals) == 6:
            return tuple(vals)  # type: ignore[return-value]
    except (TypeError, ValueError):
        pass
    return IDENTITY


@dataclass(eq=False)
class Segment:
    """A straight line or axis-aligned rectangle drawn by a path (page space)."""

    kind: str  # "line" | "rect"
    x0: float
    y0: float
    x1: float
    y1: float
    stroked: bool
    filled: bool


@dataclass(eq=False)
class Item:
    """One drawable thing on a page, tied to the operators that draw it."""

    kind: str  # text | image | inline_image | form | path | shading
    start: int  # first operator index in the page content stream
    end: int  # last operator index (inclusive)
    bbox: BBox
    text: str = ""
    size: float = 0.0
    font: str = ""
    bold: bool = False
    italic: bool = False
    invisible: bool = False
    mcid: int | None = None  # innermost enclosing MCID, if already tagged
    artifact: bool = False  # inside existing /Artifact marked content
    in_marked: bool = False  # inside any existing BMC/BDC
    form_text: bool = False  # form XObject that draws text
    form_image: bool = False  # form XObject that draws images
    segments: list[Segment] = field(default_factory=list)
    name: str = ""  # XObject resource name for Do

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass(eq=False)
class McInfo:
    """What one marked-content sequence (by MCID) contains."""

    bbox: BBox | None = None
    text: str = ""
    kinds: set[str] = field(default_factory=set)


@dataclass
class PageContent:
    ops: list
    items: list[Item]
    mcids: dict[int, McInfo]
    form_mcids: dict[tuple[tuple[int, int], int], McInfo]
    fonts_used: dict[str, FontInfo]
    unmarked: int  # text/image items neither tagged nor artifact
    has_marked_content: bool


@dataclass
class _GState:
    ctm: Matrix = IDENTITY
    font: FontInfo | None = None
    size: float = 0.0
    tc: float = 0.0
    tw: float = 0.0
    tz: float = 1.0
    tl: float = 0.0
    ts: float = 0.0
    tr: int = 0

    def copy(self) -> "_GState":
        return _GState(self.ctm, self.font, self.size, self.tc, self.tw, self.tz, self.tl, self.ts, self.tr)


@dataclass
class _Mark:
    tag: str
    mcid: int | None
    artifact: bool
    form_key: tuple[int, int] | None = None  # stream the BDC appeared in (None = page)


class _Walker:
    def __init__(self, fonts: FontCache):
        self.fonts = fonts
        self.fonts_used: dict[str, FontInfo] = {}
        self.mcids: dict[int, McInfo] = {}
        self.form_mcids: dict[tuple[tuple[int, int], int], McInfo] = {}
        self.has_marked = False

    # Record content inside a marked-content sequence with an MCID.
    def _record(self, marks: list[_Mark], kind: str, bbox: BBox, text: str) -> None:
        for mark in reversed(marks):
            if mark.mcid is not None:
                if mark.form_key is None:
                    info = self.mcids.setdefault(mark.mcid, McInfo())
                else:
                    info = self.form_mcids.setdefault((mark.form_key, mark.mcid), McInfo())
                info.bbox = union(info.bbox, bbox)
                if text:
                    info.text += text
                info.kinds.add(kind)
                return

    def walk(
        self,
        ops: list,
        resources: pikepdf.Object | None,
        gs: _GState,
        marks: list[_Mark],
        top_level: bool,
        form_key=None,
        depth: int = 0,
        seen: frozenset = frozenset(),
    ) -> tuple[list[Item], dict]:
        """Walk operators. Returns top-level items and a summary of content."""
        items: list[Item] = []
        summary = {"text": [], "sizes": [], "bold": 0, "chars": 0, "image": False, "text_any": False,
                   "bbox": None}
        stack: list[_GState] = []
        tm: Matrix = IDENTITY
        tlm: Matrix = IDENTITY
        path_start: int | None = None
        path_points: list[tuple[float, float]] = []
        path_segments: list[tuple] = []
        cur: tuple[float, float] | None = None
        subpath_start: tuple[float, float] | None = None
        local_marks = list(marks)
        props_res = resources.get("/Properties") if resources is not None else None
        xobjects = resources.get("/XObject") if resources is not None else None
        font_res = resources.get("/Font") if resources is not None else None

        def in_artifact() -> bool:
            return any(m.artifact for m in local_marks)

        def cur_mcid() -> int | None:
            for m in reversed(local_marks):
                if m.mcid is not None:
                    return m.mcid
            return None

        def add_item(item: Item) -> None:
            item.mcid = cur_mcid() if form_key is None else None
            item.artifact = in_artifact()
            item.in_marked = bool(local_marks)
            if top_level:
                items.append(item)
            self._record(local_marks, item.kind, item.bbox, item.text)
            summary["bbox"] = union(summary["bbox"], item.bbox)

        def show_text(index: int, operand_list) -> None:
            font = gs.font or self.fonts.get(None)
            size = gs.size
            th = gs.tz
            pieces: list[str] = []
            advance = 0.0
            nonlocal tm
            start_tm = tm
            for part in operand_list:
                if not isinstance(part, pikepdf.String):
                    try:
                        adj = float(part)
                    except (TypeError, ValueError):
                        continue
                    shift = -adj / 1000.0 * size * th
                    advance += shift
                    if -adj > 200 and pieces and not pieces[-1].endswith(" "):
                        pieces.append(" ")
                    continue
                data = bytes(part)
                for glyph in font.decode(data):
                    tx = (glyph.width * size + gs.tc + (gs.tw if glyph.is_space else 0.0)) * th
                    advance += tx
                    pieces.append(glyph.text)
            tm = mul((1.0, 0.0, 0.0, 1.0, advance, 0.0), start_tm)
            text = "".join(pieces)
            full = mul(start_tm, gs.ctm)
            y0 = gs.ts + font.descent * size
            y1 = gs.ts + font.ascent * size
            if advance == 0.0:
                advance = 0.01
            box = bbox_of([apply(full, 0, y0), apply(full, advance, y0), apply(full, 0, y1), apply(full, advance, y1)])
            eff = abs(size) * math.hypot(full[2], full[3])
            invisible = gs.tr in (3, 7)
            summary["text_any"] = True
            visible_chars = len(text.strip())
            if visible_chars:
                summary["text"].append(text)
                summary["sizes"].append((eff, visible_chars))
                summary["chars"] += visible_chars
                if font.bold:
                    summary["bold"] += visible_chars
            add_item(Item("text", index, index, box, text, eff, font.base_name, font.bold, font.italic, invisible))

        def finish_path(index: int, op: str) -> None:
            nonlocal path_start, path_points, path_segments
            if path_start is None:
                path_start = index
            if path_points:
                box = bbox_of(path_points)
            else:
                box = (0.0, 0.0, 0.0, 0.0)
            stroked = op in ("S", "s", "B", "B*", "b", "b*")
            filled = op in ("f", "F", "f*", "B", "B*", "b", "b*")
            segs = []
            if op != "n":
                for seg in path_segments:
                    if seg[0] == "rect":
                        _, pts = seg
                        bx = bbox_of(pts)
                        aligned = all(
                            (abs(p[0] - bx[0]) < 0.01 or abs(p[0] - bx[2]) < 0.01)
                            and (abs(p[1] - bx[1]) < 0.01 or abs(p[1] - bx[3]) < 0.01)
                            for p in pts
                        )
                        if aligned:
                            segs.append(Segment("rect", *bx, stroked, filled))
                    else:
                        _, p0, p1 = seg
                        segs.append(Segment("line", p0[0], p0[1], p1[0], p1[1], stroked, filled))
            item = Item("path", path_start, index, box)
            item.segments = segs
            add_item(item)
            path_start = None
            path_points = []
            path_segments = []

        for index, instr in enumerate(ops):
            try:
                op = str(instr.operator)
                operands = instr.operands
            except Exception:  # noqa: BLE001 - garbage operator bytes
                continue
            try:
                if op == "INLINE IMAGE":
                    box = transform_bbox(gs.ctm, (0, 0, 1, 1))
                    summary["image"] = True
                    add_item(Item("inline_image", index, index, box))
                    continue
                if op in PATH_CONSTRUCT:
                    if path_start is None:
                        path_start = index
                    if op == "re" and len(operands) == 4:
                        x, y, w, h = (_num(v) for v in operands)
                        pts = [apply(gs.ctm, x, y), apply(gs.ctm, x + w, y), apply(gs.ctm, x + w, y + h),
                               apply(gs.ctm, x, y + h)]
                        path_points.extend(pts)
                        path_segments.append(("rect", pts))
                        cur = subpath_start = pts[0]
                    elif op == "m" and len(operands) >= 2:
                        cur = subpath_start = apply(gs.ctm, _num(operands[0]), _num(operands[1]))
                        path_points.append(cur)
                    elif op == "l" and len(operands) >= 2:
                        pt = apply(gs.ctm, _num(operands[0]), _num(operands[1]))
                        if cur is not None:
                            path_segments.append(("line", cur, pt))
                        path_points.append(pt)
                        cur = pt
                    elif op in ("c", "v", "y") and len(operands) >= 4:
                        vals = [_num(v) for v in operands]
                        pts = [apply(gs.ctm, vals[k], vals[k + 1]) for k in range(0, len(vals) - 1, 2)]
                        path_points.extend(pts)
                        cur = pts[-1]
                    elif op == "h" and cur is not None and subpath_start is not None:
                        path_segments.append(("line", cur, subpath_start))
                        cur = subpath_start
                    continue
                if op in ("W", "W*"):
                    continue
                if op in PATH_PAINT:
                    finish_path(index, op)
                    continue
                if op == "q":
                    stack.append(gs.copy())
                elif op == "Q":
                    if stack:
                        gs = stack.pop()
                elif op == "cm" and len(operands) == 6:
                    gs.ctm = mul(_matrix(operands), gs.ctm)
                elif op == "BT":
                    tm = tlm = IDENTITY
                elif op == "Tf" and len(operands) >= 2:
                    font_obj = None
                    if font_res is not None:
                        font_obj = font_res.get(str(operands[0]))
                    gs.font = self.fonts.get(font_obj)
                    gs.size = _num(operands[1])
                    self.fonts_used[str(operands[0]) + repr(font_obj.objgen if font_obj is not None and font_obj.is_indirect else "")] = gs.font
                elif op == "Tc" and operands:
                    gs.tc = _num(operands[0])
                elif op == "Tw" and operands:
                    gs.tw = _num(operands[0])
                elif op == "Tz" and operands:
                    gs.tz = _num(operands[0]) / 100.0
                elif op == "TL" and operands:
                    gs.tl = _num(operands[0])
                elif op == "Ts" and operands:
                    gs.ts = _num(operands[0])
                elif op == "Tr" and operands:
                    gs.tr = int(_num(operands[0]))
                elif op == "Td" and len(operands) == 2:
                    tlm = mul((1, 0, 0, 1, _num(operands[0]), _num(operands[1])), tlm)
                    tm = tlm
                elif op == "TD" and len(operands) == 2:
                    gs.tl = -_num(operands[1])
                    tlm = mul((1, 0, 0, 1, _num(operands[0]), _num(operands[1])), tlm)
                    tm = tlm
                elif op == "Tm" and len(operands) == 6:
                    tlm = tm = _matrix(operands)
                elif op == "T*":
                    tlm = mul((1, 0, 0, 1, 0, -gs.tl), tlm)
                    tm = tlm
                elif op == "Tj" and operands:
                    show_text(index, [operands[0]])
                elif op == "TJ" and operands:
                    show_text(index, list(operands[0]))
                elif op == "'" and operands:
                    tlm = mul((1, 0, 0, 1, 0, -gs.tl), tlm)
                    tm = tlm
                    show_text(index, [operands[0]])
                elif op == '"' and len(operands) == 3:
                    gs.tw = _num(operands[0])
                    gs.tc = _num(operands[1])
                    tlm = mul((1, 0, 0, 1, 0, -gs.tl), tlm)
                    tm = tlm
                    show_text(index, [operands[2]])
                elif op in ("BMC", "BDC"):
                    self.has_marked = True
                    tag = str(operands[0]) if operands else ""
                    mcid = None
                    if op == "BDC" and len(operands) > 1:
                        props = operands[1]
                        if isinstance(props, pikepdf.Name) and props_res is not None:
                            props = props_res.get(str(props))
                        if isinstance(props, pikepdf.Dictionary) and "/MCID" in props:
                            try:
                                mcid = int(props["/MCID"])
                            except (TypeError, ValueError):
                                mcid = None
                    local_marks.append(_Mark(tag, mcid, tag == "/Artifact", form_key))
                elif op == "EMC":
                    if len(local_marks) > len(marks):
                        local_marks.pop()
                elif op == "sh":
                    box = transform_bbox(gs.ctm, (0, 0, 1, 1))
                    add_item(Item("shading", index, index, box))
                elif op == "Do" and operands:
                    name = str(operands[0])
                    xobj = xobjects.get(name) if xobjects is not None else None
                    if xobj is None:
                        continue
                    subtype = str(xobj.get("/Subtype", ""))
                    if subtype == "/Image":
                        box = transform_bbox(gs.ctm, (0, 0, 1, 1))
                        summary["image"] = True
                        item = Item("image", index, index, box)
                        item.name = name
                        add_item(item)
                    elif subtype == "/Form":
                        self._do_form(index, name, xobj, gs, local_marks, top_level, form_key, depth, seen,
                                      resources, add_item, summary)
            except Exception:  # noqa: BLE001 - one bad operator must not stop the walk
                continue
        return items, summary

    def _do_form(self, index, name, xobj, gs, local_marks, top_level, form_key, depth, seen, resources,
                 add_item, summary) -> None:
        key = xobj.objgen
        if depth > 8 or key in seen:
            return
        matrix = _matrix(xobj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
        sub_gs = gs.copy()
        sub_gs.ctm = mul(matrix, gs.ctm)
        form_res = xobj.get("/Resources", resources)
        try:
            sub_ops = pikepdf.parse_content_stream(xobj)
        except Exception:  # noqa: BLE001
            sub_ops = []
        _, sub = self.walk(sub_ops, form_res, sub_gs, local_marks, False,
                           form_key=key if "/StructParents" in xobj else form_key,
                           depth=depth + 1, seen=seen | {key})
        bbox_arr = xobj.get("/BBox")
        if bbox_arr is not None and len(bbox_arr) == 4:
            fbox = transform_bbox(sub_gs.ctm, tuple(_num(v) for v in bbox_arr))  # type: ignore[arg-type]
            box = fbox if sub["bbox"] is None else _intersect(fbox, sub["bbox"]) or fbox
        else:
            box = sub["bbox"] or transform_bbox(gs.ctm, (0, 0, 1, 1))
        text = " ".join(t.strip() for t in sub["text"] if t.strip())
        size = 0.0
        if sub["sizes"]:
            # character-weighted median size
            sizes = sorted(sub["sizes"])
            total = sum(n for _, n in sizes)
            acc = 0
            for s, n in sizes:
                acc += n
                if acc >= total / 2:
                    size = s
                    break
        item = Item("form", index, index, box, text, size)
        item.bold = sub["chars"] > 0 and sub["bold"] >= sub["chars"] / 2
        item.form_text = bool(sub["chars"])
        item.form_image = bool(sub["image"])
        item.name = name
        summary["image"] = summary["image"] or sub["image"]
        if sub["chars"]:
            summary["text"].append(text)
            summary["sizes"].extend(sub["sizes"])
            summary["chars"] += sub["chars"]
            summary["bold"] += sub["bold"]
        add_item(item)


def _intersect(a: BBox, b: BBox) -> BBox | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def analyze_page(page: pikepdf.Page, fonts: FontCache | None = None, ops: list | None = None) -> PageContent:
    """Analyze one page. Pass `ops` to reuse an already-parsed content stream."""
    fonts = fonts or FontCache()
    if ops is None:
        try:
            ops = pikepdf.parse_content_stream(page)
        except Exception:  # noqa: BLE001
            ops = []
    walker = _Walker(fonts)
    resources = page.obj.get("/Resources")
    items, _ = walker.walk(ops, resources, _GState(), [], True)
    unmarked = sum(
        1
        for it in items
        if it.kind in ("text", "image", "inline_image", "form")
        and it.mcid is None
        and not it.artifact
        and (it.kind != "text" or it.text.strip())
    )
    return PageContent(ops, items, walker.mcids, walker.form_mcids, walker.fonts_used, unmarked,
                       walker.has_marked)


def page_box(page: pikepdf.Page) -> BBox:
    """The visible page area (CropBox, falling back to MediaBox)."""
    obj = page.obj
    box = None
    for key in ("/CropBox", "/MediaBox"):
        node = obj
        while node is not None and box is None:
            if key in node:
                box = node[key]
                break
            node = node.get("/Parent")
        if box is not None:
            break
    if box is None or len(box) != 4:
        return (0.0, 0.0, 612.0, 792.0)
    x0, y0, x1, y1 = (_num(v) for v in box)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def page_rotation(page: pikepdf.Page) -> int:
    node = page.obj
    while node is not None:
        if "/Rotate" in node:
            try:
                return int(node["/Rotate"]) % 360
            except (TypeError, ValueError):
                return 0
        node = node.get("/Parent")
    return 0


def to_view_fraction(bbox: BBox, box: BBox, rotate: int) -> tuple[float, float, float, float]:
    """Convert a page-space bbox to [left, top, right, bottom] fractions of the
    displayed (rotated) page, origin top-left."""
    px0, py0, px1, py1 = box
    w, h = (px1 - px0) or 1.0, (py1 - py0) or 1.0
    x0, y0, x1, y1 = bbox
    # unrotated fractions, origin top-left
    l, r = (x0 - px0) / w, (x1 - px0) / w
    t, b = (py1 - y1) / h, (py1 - y0) / h
    if rotate == 90:
        l, t, r, b = 1 - b, l, 1 - t, r
    elif rotate == 180:
        l, t, r, b = 1 - r, 1 - b, 1 - l, 1 - t
    elif rotate == 270:
        l, t, r, b = t, 1 - r, b, 1 - l
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
    return (round(clamp(l), 4), round(clamp(t), 4), round(clamp(r), 4), round(clamp(b), 4))
