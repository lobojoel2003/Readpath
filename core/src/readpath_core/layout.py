# SPDX-License-Identifier: Apache-2.0
"""Infer document structure from page layout.

Given the items found by :mod:`readpath_core.content`, this module groups text
into lines and paragraphs, finds ruled tables, figures, lists, headings, and
repeated page headers/footers, and puts everything in reading order. It only
decides *what* things are; :mod:`readpath_core.tagger` writes the tags.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .content import BBox, Item, PageContent, union

BULLET_CHARS = "•◦▪▫‣●○■□⁃∙·➢✓✔►▸❖♦◆*–-"
_NUMBER_RE = re.compile(r"^\(?([0-9]{1,3}|[a-zA-Z]|[ivxlcIVXLC]{1,6})[.)]\s*$")
_NUMBER_START_RE = re.compile(r"^\(?([0-9]{1,3}|[a-zA-Z]|[ivxlcIVXLC]{1,6})[.)]\s+\S")
_PAGE_NUM_RE = re.compile(r"^(page\s*)?[0-9ivxlc]{1,4}(\s*(of|/)\s*[0-9]{1,4})?$", re.I)
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class Line:
    items: list[Item]
    bbox: BBox
    size: float
    bold: bool
    text: str
    bullet: Item | None = None  # a small shape drawn just left of the line (vector bullet)


@dataclass(eq=False)
class Cell:
    row: int
    col: int
    row_span: int
    col_span: int
    bbox: BBox
    items: list[Item] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _join_items(sorted(self.items, key=lambda it: (-round(it.bbox[3], 0), it.bbox[0])))


@dataclass(eq=False)
class Grid:
    bbox: BBox
    n_rows: int
    n_cols: int
    cells: list[Cell]


@dataclass(eq=False)
class Block:
    """A unit of content in reading order."""

    kind: str  # text | figure | table | form_text | annot
    page: int
    bbox: BBox
    items: list[Item] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    size: float = 0.0
    bold: bool = False
    role: str = "P"  # P, H1..H6, LI, Figure, Table, Artifact, Form, Annot, Link
    artifact_kind: str | None = None  # Header / Footer / Background / Decoration
    grid: Grid | None = None
    label_items: list[Item] = field(default_factory=list)  # list bullet/number glyphs
    numbering: str | None = None
    annot: object | None = None  # pikepdf annotation for Form/Annot/Link blocks
    order: float = 0.0

    @property
    def text(self) -> str:
        if self.lines:
            return " ".join(line.text for line in self.lines).strip()
        if self.grid is not None:
            return " ".join(c.text for c in self.grid.cells if c.items)
        return " ".join(it.text.strip() for it in self.items if it.text.strip())

    @property
    def start(self) -> int:
        return min((it.start for it in self.items), default=10**9)


@dataclass(eq=False)
class PageLayout:
    page: int
    box: BBox
    blocks: list[Block]  # in reading order (artifacts excluded)
    artifacts: list[Block]
    absorb: list[Item]  # whitespace text and clipping paths
    order_method: str = "content"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join_items(items: list[Item]) -> str:
    out = ""
    prev: Item | None = None
    for it in items:
        txt = it.text
        if prev is not None and out and not out.endswith(" ") and not txt.startswith(" "):
            gap = it.bbox[0] - prev.bbox[2]
            same_line = abs(it.bbox[1] - prev.bbox[1]) < max(it.size, prev.size) * 0.5
            if not same_line or gap > 0.12 * max(it.size, 1.0):
                out += " "
        out += txt
        prev = it
    return re.sub(r"\s+", " ", out).strip()


def _weighted_median(pairs: list[tuple[float, int]]) -> float:
    if not pairs:
        return 0.0
    pairs = sorted(pairs)
    total = sum(n for _, n in pairs)
    acc = 0
    for value, n in pairs:
        acc += n
        if acc >= total / 2:
            return value
    return pairs[-1][0]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(item: Item, box: BBox, pad: float = 1.0) -> bool:
    return box[0] - pad <= item.cx <= box[2] + pad and box[1] - pad <= item.cy <= box[3] + pad


def is_bullet_text(text: str) -> bool:
    t = text.strip()
    return bool(t) and (all(ch in BULLET_CHARS for ch in t) or bool(_NUMBER_RE.match(t)))


def list_start(text: str) -> str | None:
    """Return the numbering style if the text starts like a list item."""
    t = text.strip()
    if not t:
        return None
    if t[0] in BULLET_CHARS and (len(t) == 1 or t[1] in " \t " or t[0] not in "-*–"):
        if t[0] in "-*–" and (len(t) < 2 or t[1] not in " \t "):
            return None
        return "Disc"
    m = _NUMBER_START_RE.match(t)
    if m:
        token = m.group(1)
        if token.isdigit():
            return "Decimal"
        if re.fullmatch(r"[ivxlc]+", token):
            return "LowerRoman"
        if re.fullmatch(r"[IVXLC]+", token):
            return "UpperRoman"
        return "LowerAlpha" if token.islower() else "UpperAlpha"
    return None


# ---------------------------------------------------------------------------
# Lines and paragraphs
# ---------------------------------------------------------------------------


def build_lines(items: list[Item]) -> list[Line]:
    texts = [it for it in items if it.kind == "text" and it.text.strip()]
    texts.sort(key=lambda it: (-round(it.bbox[3], 1), it.bbox[0]))
    lines: list[dict] = []
    for it in texts:
        best = None
        best_gap = None
        for ln in reversed(lines[-40:]):
            y0, y1 = ln["bbox"][1], ln["bbox"][3]
            h = min(y1 - y0, it.height)
            if h <= 0:
                continue
            if _overlap(y0, y1, it.bbox[1], it.bbox[3]) < 0.5 * h:
                continue
            size = max(ln["size"], it.size, 1.0)
            if it.bbox[0] >= ln["bbox"][2]:
                gap = it.bbox[0] - ln["bbox"][2]
            elif it.bbox[2] <= ln["bbox"][0]:
                gap = ln["bbox"][0] - it.bbox[2]
            else:
                gap = 0.0
            if gap > 1.5 * size:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = ln, gap
        if best is None:
            lines.append({"items": [it], "bbox": it.bbox, "size": it.size})
        else:
            best["items"].append(it)
            best["bbox"] = union(best["bbox"], it.bbox)
            best["size"] = max(best["size"], it.size)
    # A lone bullet or number with its text further right (hanging indent) is one line
    for ln in list(lines):
        if ln not in lines or not is_bullet_text(_join_items(ln["items"])):
            continue
        size = max(ln["size"], 1.0)
        best, best_gap = None, None
        for other in lines:
            if other is ln:
                continue
            h = min(ln["bbox"][3] - ln["bbox"][1], other["bbox"][3] - other["bbox"][1])
            if h <= 0 or _overlap(ln["bbox"][1], ln["bbox"][3], other["bbox"][1], other["bbox"][3]) < 0.5 * h:
                continue
            gap = other["bbox"][0] - ln["bbox"][2]
            if 0 <= gap <= 4 * size and (best_gap is None or gap < best_gap):
                best, best_gap = other, gap
        if best is not None:
            best["items"] = ln["items"] + best["items"]
            best["bbox"] = union(best["bbox"], ln["bbox"])
            lines.remove(ln)
    out: list[Line] = []
    for ln in lines:
        its = sorted(ln["items"], key=lambda it: it.bbox[0])
        weights = [(it.size, max(1, len(it.text.strip()))) for it in its]
        chars = sum(len(it.text.strip()) for it in its)
        bold_chars = sum(len(it.text.strip()) for it in its if it.bold)
        out.append(Line(its, ln["bbox"], _weighted_median(weights), chars > 0 and bold_chars >= 0.8 * chars,
                        _join_items(its)))
    return out


def build_paragraphs(lines: list[Line], page: int) -> list[Block]:
    lines = sorted(lines, key=lambda ln: (-ln.bbox[3], ln.bbox[0]))
    blocks: list[Block] = []
    list_blocks: set[int] = set()
    for ln in lines:
        starts_item = list_start(ln.text) is not None or ln.bullet is not None
        best = None
        best_gap = None
        if not starts_item:
            for b in blocks:
                last = b.lines[-1]
                size = max(last.size, ln.size, 1.0)
                vgap = last.bbox[1] - ln.bbox[3]
                if vgap < -0.35 * size or vgap > 0.85 * size:
                    continue
                ratio = ln.size / max(last.size, 0.1)
                if ratio < 0.85 or ratio > 1.18:
                    continue
                if last.bold != ln.bold:
                    continue
                if ln.bold and abs(ratio - 1) > 0.05:
                    continue  # bold lines of different sizes are separate headings
                if _overlap(b.bbox[0], b.bbox[2], ln.bbox[0], ln.bbox[2]) <= 0:
                    continue
                if abs(ln.bbox[0] - b.bbox[0]) > 2.5 * size and not (b.bbox[0] <= ln.bbox[0] <= b.bbox[2]):
                    continue
                if id(b) in list_blocks and ln.bbox[0] < b.bbox[0] + 0.5 * size:
                    continue  # list continuation lines are indented past the bullet
                if best_gap is None or vgap < best_gap:
                    best, best_gap = b, vgap
        if best is None:
            blocks.append(Block("text", page, ln.bbox, list(ln.items), [ln], ln.size, ln.bold))
            if starts_item:
                list_blocks.add(id(blocks[-1]))
        else:
            best.lines.append(ln)
            best.items.extend(ln.items)
            best.bbox = union(best.bbox, ln.bbox)  # type: ignore[assignment]
    for b in blocks:
        weights = [(ln.size, max(1, len(ln.text))) for ln in b.lines]
        b.size = _weighted_median(weights)
        chars = sum(len(ln.text) for ln in b.lines)
        b.bold = chars > 0 and sum(len(ln.text) for ln in b.lines if ln.bold) >= 0.8 * chars
    return blocks


# ---------------------------------------------------------------------------
# Tables (ruled)
# ---------------------------------------------------------------------------


def _edges_from_items(items: list[Item]) -> list[dict]:
    edges: list[dict] = []

    def h(x0, x1, y):
        if x1 - x0 >= 3:
            edges.append({"x0": x0, "x1": x1, "top": -y, "bottom": -y, "width": x1 - x0, "height": 0,
                          "orientation": "h", "object_type": "line"})

    def v(x, y0, y1):
        if y1 - y0 >= 3:
            edges.append({"x0": x, "x1": x, "top": -y1, "bottom": -y0, "width": 0, "height": y1 - y0,
                          "orientation": "v", "object_type": "line"})

    for it in items:
        if it.kind != "path" or it.artifact:
            continue
        for s in it.segments:
            if s.kind == "line" and s.stroked:
                if abs(s.y1 - s.y0) < 1.0:
                    h(min(s.x0, s.x1), max(s.x0, s.x1), (s.y0 + s.y1) / 2)
                elif abs(s.x1 - s.x0) < 1.0:
                    v((s.x0 + s.x1) / 2, min(s.y0, s.y1), max(s.y0, s.y1))
            elif s.kind == "rect" and (s.stroked or s.filled):
                w, ht = s.x1 - s.x0, s.y1 - s.y0
                if ht < 2.5 and w >= 3:
                    h(s.x0, s.x1, (s.y0 + s.y1) / 2)
                elif w < 2.5 and ht >= 3:
                    v((s.x0 + s.x1) / 2, s.y0, s.y1)
                elif w >= 3 and ht >= 3:
                    h(s.x0, s.x1, s.y0)
                    h(s.x0, s.x1, s.y1)
                    v(s.x0, s.y0, s.y1)
                    v(s.x1, s.y0, s.y1)
    return edges


def _cluster(values: list[float], tol: float = 2.0) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _index(vals: list[float], v: float, tol: float = 2.0) -> int:
    best = min(range(len(vals)), key=lambda i: abs(vals[i] - v))
    return best


def find_tables(items: list[Item], text_items: list[Item]) -> list[Grid]:
    """Find tables drawn with ruling lines or cell borders."""
    try:
        from pdfplumber import table as pt
    except Exception:  # noqa: BLE001
        return []
    edges = _edges_from_items(items)
    if len(edges) < 4 or len(edges) > 20000:
        return []
    try:
        merged = pt.merge_edges(edges, 3, 3, 3, 3)
        merged = [e for e in merged if (e["x1"] - e["x0"]) >= 3 or (e["bottom"] - e["top"]) >= 3]
        inter = pt.edges_to_intersections(merged, 3, 3)
        cells = pt.intersections_to_cells(inter)
        groups = pt.cells_to_tables(cells)
    except Exception:  # noqa: BLE001
        return []
    grids: list[Grid] = []
    for group in groups:
        xs = _cluster([c[0] for c in group] + [c[2] for c in group])
        ys = _cluster([c[1] for c in group] + [c[3] for c in group])  # "top" values (= -y)
        n_cols, n_rows = len(xs) - 1, len(ys) - 1
        if n_cols < 2 or n_rows < 2:
            continue
        gcells: list[Cell] = []
        for (x0, top, x1, bottom) in group:
            c0, c1 = _index(xs, x0), _index(xs, x1)
            r0, r1 = _index(ys, top), _index(ys, bottom)
            if c1 <= c0 or r1 <= r0:
                continue
            gcells.append(Cell(r0, c0, r1 - r0, c1 - c0, (x0, -bottom, x1, -top)))
        if not gcells:
            continue
        bbox = (xs[0], -ys[-1], xs[-1], -ys[0])
        for it in text_items:
            if not _inside(it, bbox, 0.5):
                continue
            for cell in gcells:
                if _inside(it, cell.bbox, 0.5):
                    cell.items.append(it)
                    break
        filled = sum(1 for c in gcells if c.items)
        if filled < 2:
            continue
        gcells.sort(key=lambda c: (c.row, c.col))
        grids.append(Grid(bbox, n_rows, n_cols, gcells))
    return grids


# ---------------------------------------------------------------------------
# Vector bullets and unruled tables
# ---------------------------------------------------------------------------


def attach_vector_bullets(lines: list[Line], paths: list[Item]) -> set[int]:
    """Find small filled shapes drawn just left of a line (browser-style bullets).

    Returns the ids of path items used as bullets."""
    used: set[int] = set()
    candidates = [p for p in paths if 1.2 <= p.width <= 9 and 1.2 <= p.height <= 9]
    if not candidates:
        return used
    for ln in lines:
        size = max(ln.size, 1.0)
        for p in candidates:
            if id(p) in used or p.width > 0.8 * size or p.height > 0.8 * size:
                continue
            gap = ln.bbox[0] - p.bbox[2]
            if not (0 <= gap <= 2.5 * size):
                continue
            if not (ln.bbox[1] - 1 <= p.cy <= ln.bbox[3] + 1):
                continue
            ln.bullet = p
            used.add(id(p))
            break
    return used


def _rows_of(lines: list[Line]) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: -l.bbox[3]):
        if rows:
            ref = rows[-1][0]
            h = min(ref.bbox[3] - ref.bbox[1], ln.bbox[3] - ln.bbox[1])
            if h > 0 and _overlap(ref.bbox[1], ref.bbox[3], ln.bbox[1], ln.bbox[3]) >= 0.5 * h:
                rows[-1].append(ln)
                continue
        rows.append([ln])
    for r in rows:
        r.sort(key=lambda l: l.bbox[0])
    return rows


def _column_gaps(rows: list[list[Line]]) -> list[tuple[float, float]]:
    """Column intervals: x ranges covered by text, separated by gaps that run
    through every row."""
    spans = sorted((ln.bbox[0], ln.bbox[2]) for r in rows for ln in r)
    cols: list[list[float]] = []
    for x0, x1 in spans:
        if cols and x0 <= cols[-1][1] + 2:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    return [(c[0], c[1]) for c in cols]


def find_text_tables(lines: list[Line], page: int) -> list[Grid]:
    """Tables without ruling lines: 3+ rows of short cells lined up in columns."""
    rows = _rows_of(lines)
    grids: list[Grid] = []
    i = 0
    while i < len(rows):
        if len(rows[i]) < 2:
            i += 1
            continue
        j = i
        while j + 1 < len(rows) and len(rows[j + 1]) >= 2:
            size = max(ln.size for ln in rows[j])
            spacing = min(ln.bbox[1] for ln in rows[j]) - max(ln.bbox[3] for ln in rows[j + 1])
            if spacing > 1.6 * size:
                break
            cols = _column_gaps(rows[i : j + 2])
            if len(cols) < 2 or any(len({_col_of(ln, cols) for ln in r}) < len(r) for r in rows[i : j + 2]):
                break
            j += 1
        run = rows[i : j + 1]
        cols = _column_gaps(run)
        words = [len(ln.text.split()) for r in run for ln in r]
        # A column of bullets or numbers next to text is a list, not a table
        labels = all(is_bullet_text(r[0].text) for r in run)
        if len(run) >= 3 and len(cols) >= 2 and sum(words) / len(words) <= 5 and not labels:
            cells: list[Cell] = []
            for r_index, r in enumerate(run):
                top = max(ln.bbox[3] for ln in r)
                bottom = min(ln.bbox[1] for ln in r)
                by_col: dict[int, list[Item]] = {}
                for ln in r:
                    by_col.setdefault(_col_of(ln, cols), []).extend(ln.items)
                for c_index, (x0, x1) in enumerate(cols):
                    cells.append(Cell(r_index, c_index, 1, 1, (x0, bottom, x1, top), by_col.get(c_index, [])))
            box = (cols[0][0], min(ln.bbox[1] for ln in run[-1]), cols[-1][1], max(ln.bbox[3] for ln in run[0]))
            grids.append(Grid(box, len(run), len(cols), cells))
            i = j + 1
        else:
            i += 1
    return grids


def _col_of(ln: Line, cols: list[tuple[float, float]]) -> int:
    for k, (x0, x1) in enumerate(cols):
        if _overlap(x0, x1, ln.bbox[0], ln.bbox[2]) > 0:
            return k
    return -1


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------


def _inversions(blocks: list[Block]) -> float:
    if len(blocks) < 3:
        return 0.0
    bad = 0
    for a, b in zip(blocks, blocks[1:]):
        tol = max(a.size, b.size, 6.0)
        above = b.bbox[1] > a.bbox[3] + tol
        overl = _overlap(a.bbox[0], a.bbox[2], b.bbox[0], b.bbox[2]) > 0.3 * min(
            a.bbox[2] - a.bbox[0], b.bbox[2] - b.bbox[0]
        )
        if above and overl:
            bad += 1
    return bad / (len(blocks) - 1)


def _split(blocks: list[Block], axis: str, min_gap: float) -> list[list[Block]]:
    if axis == "y":
        ordered = sorted(blocks, key=lambda b: -b.bbox[3])
        groups: list[list[Block]] = []
        floor = None
        for b in ordered:
            if groups and floor is not None and b.bbox[3] < floor - min_gap:
                groups.append([b])
                floor = b.bbox[1]
            elif groups:
                groups[-1].append(b)
                floor = min(floor, b.bbox[1]) if floor is not None else b.bbox[1]
            else:
                groups.append([b])
                floor = b.bbox[1]
        return groups
    ordered = sorted(blocks, key=lambda b: b.bbox[0])
    groups = []
    right = None
    for b in ordered:
        if groups and right is not None and b.bbox[0] > right + min_gap:
            groups.append([b])
            right = b.bbox[2]
        elif groups:
            groups[-1].append(b)
            right = max(right, b.bbox[2]) if right is not None else b.bbox[2]
        else:
            groups.append([b])
            right = b.bbox[2]
    return groups


def _columnar(groups: list[list[Block]]) -> bool:
    def substantial(g: list[Block]) -> bool:
        return any(len(b.lines) >= 3 or b.kind in ("figure", "table") for b in g)

    return sum(1 for g in groups if substantial(g)) >= 2


def xy_cut(blocks: list[Block], depth: int = 0) -> list[Block]:
    if len(blocks) <= 1 or depth > 40:
        return list(blocks)
    cols = _split(blocks, "x", 6.0)
    if len(cols) > 1 and _columnar(cols):
        return [b for g in cols for b in xy_cut(g, depth + 1)]
    rows = _split(blocks, "y", 0.5)
    if len(rows) > 1:
        return [b for g in rows for b in xy_cut(g, depth + 1)]
    if len(cols) > 1:
        return [b for g in cols for b in xy_cut(g, depth + 1)]
    return sorted(blocks, key=lambda b: (-b.bbox[3], b.bbox[0]))


def order_blocks(blocks: list[Block]) -> tuple[list[Block], str]:
    by_content = sorted(blocks, key=lambda b: b.start)
    if _inversions(by_content) <= 0.15:
        return by_content, "content"
    return xy_cut(blocks), "layout"


def insert_by_position(ordered: list[Block], extra: Block) -> None:
    for i, b in enumerate(ordered):
        overl = _overlap(b.bbox[0], b.bbox[2], extra.bbox[0], extra.bbox[2]) > 0
        if overl and b.bbox[3] < extra.bbox[3] - 1:
            ordered.insert(i, extra)
            return
    ordered.append(extra)


# ---------------------------------------------------------------------------
# Page analysis
# ---------------------------------------------------------------------------


def _merge_figures(images: list[Item], page: int) -> list[Block]:
    parent = list(range(len(images)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            a, b = images[i].bbox, images[j].bbox
            if (_overlap(a[0], a[2], b[0], b[2]) > -2 and _overlap(a[1], a[3], b[1], b[3]) > -2):
                parent[find(i)] = find(j)
    groups: dict[int, list[Item]] = defaultdict(list)
    for i, it in enumerate(images):
        groups[find(i)].append(it)
    blocks = []
    for its in groups.values():
        box = None
        for it in its:
            box = union(box, it.bbox)
        blocks.append(Block("figure", page, box, sorted(its, key=lambda it: it.start), role="Figure"))
    return blocks


def find_vector_figures(paths: list[Item], text_items: list[Item], tables: list[Block], box: BBox,
                        page: int) -> list[Block]:
    """Pictures and charts drawn with vector shapes instead of images.

    A cluster of shapes becomes a figure when it is reasonably large, has at
    least three shapes (or curves), and is not mostly text (a shaded box
    behind a paragraph is not a picture). Text inside, such as chart labels,
    belongs to the figure."""
    page_area = _area(box) or 1.0
    cands = []
    for p in paths:
        w, h = p.width, p.height
        if w < 3 or h < 3 or w * h < 100 or w * h > 0.6 * page_area:
            continue
        if any(_inside(p, t.bbox, 1) for t in tables):
            continue
        cands.append(p)
    if not cands:
        return []
    parent = list(range(len(cands)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(cands)), key=lambda i: cands[i].bbox[0])
    for a_pos, i in enumerate(order):
        a = cands[i].bbox
        for j in order[a_pos + 1:]:
            b = cands[j].bbox
            if b[0] > a[2] + 6:
                break
            if _overlap(a[1], a[3], b[1], b[3]) > -6:
                parent[find(i)] = find(j)
    groups: dict[int, list[Item]] = defaultdict(list)
    for i, p in enumerate(cands):
        groups[find(i)].append(p)
    figures = []
    for group in groups.values():
        bbox = None
        for p in group:
            bbox = union(bbox, p.bbox)
        assert bbox is not None
        area = _area(bbox)
        curves = any(p.curved for p in group)
        if area < 2500 or (len(group) < 3 and not curves):
            continue
        inside = [t for t in text_items if _inside(t, bbox, 1)]
        if sum(_area(t.bbox) for t in inside) > 0.25 * area:
            continue
        items = sorted(group + inside, key=lambda it: it.start)
        figures.append(Block("figure", page, _clip(bbox, box), items, role="Figure"))
    return figures


def _clip(b: BBox, box: BBox) -> BBox:
    return (max(b[0], box[0]), max(b[1], box[1]), min(b[2], box[2]), min(b[3], box[3]))


def analyze_layout(page_index: int, content: PageContent, box: BBox) -> PageLayout:
    """Decide what each item on a page is, and the page's reading order."""
    page_area = _area(box) or 1.0
    items = [it for it in content.items if not it.artifact]
    absorb: list[Item] = []
    artifacts: list[Block] = []
    text_items: list[Item] = []
    images: list[Item] = []
    form_blocks: list[Block] = []
    has_text = any(it.kind == "text" and it.text.strip() for it in items) or any(
        it.kind == "form" and it.form_text for it in items
    )
    for it in items:
        if it.kind == "text":
            visible = _area(_clip(it.bbox, box)) > 0 or it.width == 0
            if not it.text.strip():
                absorb.append(it)
            elif not visible:
                artifacts.append(Block("text", page_index, it.bbox, [it], role="Artifact"))
            else:
                text_items.append(it)
        elif it.kind in ("image", "inline_image"):
            clipped = _clip(it.bbox, box)
            w, h = clipped[2] - clipped[0], clipped[3] - clipped[1]
            if w <= 0 or h <= 0:
                artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact"))
            elif w < 12 or h < 12 or w * h < 300:
                artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact",
                                       artifact_kind="Decoration"))
            elif has_text and _area(clipped) > 0.6 * page_area:
                artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact",
                                       artifact_kind="Background"))
            else:
                images.append(it)
        elif it.kind == "form":
            if it.form_text:
                form_blocks.append(Block("form_text", page_index, _clip(it.bbox, box), [it], [], it.size,
                                         it.bold))
            elif it.form_image and _area(_clip(it.bbox, box)) >= 300 and not (
                has_text and _area(_clip(it.bbox, box)) > 0.6 * page_area
            ):
                images.append(it)
            else:
                artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact"))
        elif it.kind == "path":
            if _is_clip_only(content, it):
                absorb.append(it)
            else:
                artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact"))
        else:  # shading
            artifacts.append(Block("figure", page_index, it.bbox, [it], role="Artifact"))

    grids = find_tables(items, text_items)
    in_table: set[int] = set()
    table_blocks: list[Block] = []
    for grid in grids:
        cell_items = [it for c in grid.cells for it in c.items]
        in_table.update(id(it) for it in cell_items)
        table_blocks.append(Block("table", page_index, grid.bbox, cell_items, role="Table", grid=grid))
    remaining = [it for it in text_items if id(it) not in in_table]
    path_items = [a.items[0] for a in artifacts if a.items and a.items[0].kind == "path" and len(a.items) == 1]
    vector_figures = find_vector_figures(path_items, remaining, table_blocks, box, page_index)
    if vector_figures:
        used_ids = {id(it) for f in vector_figures for it in f.items}
        artifacts = [a for a in artifacts if not (a.items and id(a.items[0]) in used_ids)]
        remaining = [it for it in remaining if id(it) not in used_ids]
    lines = build_lines(remaining)
    for grid in find_text_tables(lines, page_index):
        cell_items = [it for c in grid.cells for it in c.items]
        in_table.update(id(it) for it in cell_items)
        table_blocks.append(Block("table", page_index, grid.bbox, cell_items, role="Table", grid=grid))
    lines = [ln for ln in lines if not any(id(it) in in_table for it in ln.items)]
    path_artifacts = [a for a in artifacts if a.items and a.items[0].kind == "path" and len(a.items) == 1]
    used = attach_vector_bullets(lines, [a.items[0] for a in path_artifacts])
    if used:
        artifacts = [a for a in artifacts if not (a in path_artifacts and id(a.items[0]) in used)]
    paragraphs = build_paragraphs(lines, page_index)
    figures = _merge_figures(images, page_index)
    for f in figures:
        f.bbox = _clip(f.bbox, box)
    blocks = paragraphs + form_blocks + table_blocks + figures + vector_figures
    return PageLayout(page_index, box, blocks, artifacts, absorb)


def _is_clip_only(content: PageContent, item: Item) -> bool:
    try:
        return str(content.ops[item.end].operator) == "n"
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Document-level decisions
# ---------------------------------------------------------------------------


def _norm_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def mark_headers_footers(layouts: list[PageLayout]) -> None:
    n_pages = len(layouts)
    counts: Counter[str] = Counter()
    candidates: list[tuple[PageLayout, Block, str, str]] = []
    for lay in layouts:
        h = lay.box[3] - lay.box[1]
        seen: set[str] = set()
        for b in lay.blocks:
            if b.kind not in ("text", "form_text") or len(b.lines) > 2:
                continue
            if b.bbox[1] >= lay.box[3] - 0.09 * h:
                where = "Header"
            elif b.bbox[3] <= lay.box[1] + 0.09 * h:
                where = "Footer"
            else:
                continue
            key = _norm_repeat(b.text)
            candidates.append((lay, b, key, where))
            if key not in seen:
                counts[key] += 1
                seen.add(key)
    for _lay, b, key, where in candidates:
        repeated = n_pages >= 2 and counts[key] >= 2 and counts[key] >= 0.5 * n_pages
        if repeated or _PAGE_NUM_RE.match(b.text.strip()):
            b.role = "Artifact"
            b.artifact_kind = where
    for lay in layouts:
        moved = [b for b in lay.blocks if b.role == "Artifact"]
        lay.blocks = [b for b in lay.blocks if b.role != "Artifact"]
        lay.artifacts.extend(moved)


def body_size(layouts: list[PageLayout]) -> float:
    weights: Counter[float] = Counter()
    for lay in layouts:
        for b in lay.blocks:
            for ln in b.lines:
                weights[round(ln.size * 2) / 2] += len(ln.text)
    if not weights:
        return 10.0
    return weights.most_common(1)[0][0]


def classify(layouts: list[PageLayout]) -> None:
    """Assign heading levels and list roles, in reading order."""
    body = body_size(layouts)
    styles: set[tuple[float, bool]] = set()
    candidates: list[Block] = []
    for lay in layouts:
        for b in lay.blocks:
            if b.kind not in ("text", "form_text"):
                continue
            text = b.text
            letters = len(_LETTER_RE.findall(text))
            if letters < 2:
                continue
            if b.kind == "text" and b.lines and b.lines[0].bullet is not None:
                b.role = "LI"
                b.numbering = "Disc"
                b.label_items = [b.lines[0].bullet]
                b.items.insert(0, b.lines[0].bullet)
                continue
            if b.kind == "text" and list_start(text):
                b.role = "LI"
                b.numbering = list_start(text)
                first = b.lines[0].items[0] if b.lines and b.lines[0].items else None
                if first is not None and is_bullet_text(first.text) and len(b.lines[0].items) > 1:
                    b.label_items = [first]
                continue
            n_lines = max(1, len(b.lines))
            stripped = text.rstrip()
            heading = False
            if n_lines <= 3 and len(text) <= 200 and b.size >= body * 1.15:
                heading = True
            elif (b.bold and n_lines <= 2 and len(text) <= 120 and b.size >= body * 0.95
                  and not stripped.endswith((".", ",", ";")) and letters >= 3):
                heading = True
            if heading and stripped.endswith(":") and b.size < body * 1.15:
                heading = False
            if heading:
                candidates.append(b)
                styles.add((round(b.size * 2) / 2, b.bold))
    ranked = sorted(styles, key=lambda s: (-s[0], not s[1]))
    level_of = {s: min(6, i + 1) for i, s in enumerate(ranked)}
    prev = 0
    for lay in layouts:
        for b in lay.blocks:
            if b in candidates:
                level = level_of[(round(b.size * 2) / 2, b.bold)]
                level = min(level, prev + 1)
                b.role = f"H{level}"
                prev = level
            elif b.role == "LI":
                continue
            elif b.kind in ("text", "form_text") and b.role not in ("Artifact",):
                b.role = "P"


def analyze_document(page_contents: list[tuple[PageContent, BBox]]) -> list[PageLayout]:
    layouts = [analyze_layout(i, pc, box) for i, (pc, box) in enumerate(page_contents)]
    mark_headers_footers(layouts)
    for lay in layouts:
        lay.blocks, lay.order_method = order_blocks(lay.blocks)
    classify(layouts)
    return layouts
