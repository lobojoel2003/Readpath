# SPDX-License-Identifier: Apache-2.0
"""Add tags (a structure tree) to an untagged PDF.

Steps:
1. analyze every page (:mod:`content`) and infer structure (:mod:`layout`);
2. decide which structure element owns each drawn item;
3. rewrite each page's content stream, wrapping owned items in marked content
   (``/P <</MCID n>> BDC ... EMC``) and everything else in ``/Artifact``;
4. write the StructTreeRoot, ParentTree, and annotation links.

Wrapping happens at the deepest safe level so marked content always nests
properly with BT/ET, q/Q and any existing marked content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pikepdf
from pikepdf import Array, Dictionary, Name, Operator

from .content import BBox, Item, PageContent, analyze_page, page_box
from .fonts import FontCache
from .layout import Block, PageLayout, analyze_document, insert_by_position

# Owner markers for items that are not owned by a structure element
ABSORB = "absorb"  # whitespace / clipping: joins neighbouring runs, else artifact
EXISTING = "existing-artifact"


@dataclass(eq=False)
class SE:
    """An in-memory structure element, serialized to the PDF at the end."""

    type: str
    kids: list = field(default_factory=list)  # SE | MCRef | ObjRef
    page: int | None = None
    alt: str | None = None
    attrs: list[dict] = field(default_factory=list)
    title: str | None = None
    obj: pikepdf.Object | None = None

    def first_pos(self) -> tuple[int, int]:
        best = (10**9, 10**9)
        for k in self.kids:
            pos = k.first_pos() if isinstance(k, SE) else k.pos
            best = min(best, pos)
        return best


@dataclass
class MCRef:
    page: int
    mcid: int
    pos: tuple[int, int]


@dataclass
class ObjRef:
    page: int
    annot: pikepdf.Object
    pos: tuple[int, int]


@dataclass
class Artifact:
    kind: str | None = None  # Header / Footer / Background / Decoration / None

    def __hash__(self) -> int:
        return hash(("artifact", self.kind))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Artifact) and other.kind == self.kind


@dataclass
class TagResult:
    tagged: bool
    pages: int
    elements: int
    headings: int
    figures: int
    tables: int
    lists: int
    links: int
    order_methods: list[str]
    title_guess: str | None
    message: str = ""


# ---------------------------------------------------------------------------
# Content stream rewriting
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Node:
    start: int
    end: int
    kind: str  # "leaf" | "BT" | "q" | "MC"
    children: list["_Node"] = field(default_factory=list)
    owner: object = None  # for leaves
    artifact_mc: bool = False


def _build_tree(ops: list, leaf_of: dict[int, tuple[int, object]]) -> _Node | None:
    """Nest operators into BT/q/marked-content groups. None if unbalanced."""
    root = _Node(0, len(ops) - 1, "root")
    stack = [root]
    i = 0
    n = len(ops)
    while i < n:
        if i in leaf_of:
            end, owner = leaf_of[i]
            stack[-1].children.append(_Node(i, end, "leaf", owner=owner))
            i = end + 1
            continue
        op = str(ops[i].operator)
        if op in ("BT", "q", "BMC", "BDC"):
            kind = "MC" if op in ("BMC", "BDC") else op
            node = _Node(i, i, kind)
            if kind == "MC" and ops[i].operands and str(ops[i].operands[0]) == "/Artifact":
                node.artifact_mc = True
            stack[-1].children.append(node)
            stack.append(node)
        elif op in ("ET", "Q", "EMC"):
            want = {"ET": "BT", "Q": "q", "EMC": "MC"}[op]
            if stack[-1].kind != want:
                return None
            stack[-1].end = i
            stack.pop()
        else:
            stack[-1].children.append(_Node(i, i, "leaf", owner=None))
        i += 1
    if len(stack) != 1:
        return None
    return root


_MIXED = object()


def _owner_of(node: _Node, memo: dict[int, object]) -> object:
    """Uniform owner of a subtree: an owner, None (no content), or _MIXED."""
    key = id(node)
    if key in memo:
        return memo[key]
    if node.kind == "leaf":
        result = None if node.owner in (None, ABSORB) else node.owner
    elif node.artifact_mc:
        result = EXISTING
    else:
        result = None
        for child in node.children:
            o = _owner_of(child, memo)
            if o is None:
                continue
            if o is _MIXED or (result is not None and o != result):
                result = _MIXED
                break
            result = o
    memo[key] = result
    return result


class _Emitter:
    def __init__(self, ops: list, page_index: int, tag_of, new_mcid):
        self.ops = ops
        self.out: list = []
        self.page = page_index
        self.tag_of = tag_of  # owner -> (tag name, properties or None)
        self.new_mcid = new_mcid  # (owner, pos) -> mcid
        self.memo: dict[int, object] = {}

    def _copy(self, node: _Node) -> None:
        self.out.extend(self.ops[node.start : node.end + 1] if node.kind == "leaf" else [])

    def _emit_node(self, node: _Node, wrapped: bool) -> None:
        if node.kind == "leaf":
            if not wrapped and node.owner == ABSORB:
                self._open(Artifact(), node.start)
                self.out.extend(self.ops[node.start : node.end + 1])
                self.out.append(pikepdf.ContentStreamInstruction([], Operator("EMC")))
            else:
                self.out.extend(self.ops[node.start : node.end + 1])
            return
        if node.kind != "root":
            self.out.append(self.ops[node.start])
        if wrapped or node.artifact_mc:
            for child in node.children:
                self._emit_node(child, True)
        else:
            self._emit_children(node.children)
        if node.kind != "root":
            self.out.append(self.ops[node.end])

    def _open(self, owner, pos: int) -> None:
        tag, props = self.tag_of(owner)
        if isinstance(owner, Artifact) or owner == EXISTING:
            if props is None:
                self.out.append(pikepdf.ContentStreamInstruction([Name(tag)], Operator("BMC")))
            else:
                self.out.append(pikepdf.ContentStreamInstruction([Name(tag), props], Operator("BDC")))
            return
        mcid = self.new_mcid(owner, (self.page, pos))
        self.out.append(
            pikepdf.ContentStreamInstruction([Name(tag), Dictionary(MCID=mcid)], Operator("BDC"))
        )

    def _emit_children(self, children: list[_Node]) -> None:
        i = 0
        n = len(children)
        while i < n:
            child = children[i]
            owner = _owner_of(child, self.memo)
            if owner is _MIXED:
                self._emit_node(child, False)
                i += 1
                continue
            if owner is None or owner == EXISTING:
                self._emit_node(child, owner == EXISTING)
                i += 1
                continue
            # start a run of children sharing this owner (or with no content)
            j = i
            last = i
            while j + 1 < n:
                nxt = _owner_of(children[j + 1], self.memo)
                if nxt is None:
                    j += 1
                    continue
                if nxt == owner:
                    j += 1
                    last = j
                    continue
                break
            self._open(owner, child.start)
            for k in range(i, last + 1):
                self._emit_node(children[k], True)
            self.out.append(pikepdf.ContentStreamInstruction([], Operator("EMC")))
            i = last + 1

    def emit_leafwise(self, leaf_of: dict[int, tuple[int, object]]) -> None:
        """Fallback for unbalanced streams: wrap each item on its own."""
        i = 0
        n = len(self.ops)
        while i < n:
            if i in leaf_of:
                end, owner = leaf_of[i]
                if owner is None:
                    self.out.extend(self.ops[i : end + 1])
                else:
                    self._open(Artifact() if owner == ABSORB else owner, i)
                    self.out.extend(self.ops[i : end + 1])
                    self.out.append(pikepdf.ContentStreamInstruction([], Operator("EMC")))
                i = end + 1
            else:
                self.out.append(self.ops[i])
                i += 1


def _strip_mcids(ops: list) -> list:
    """Remove stray MCIDs from marked content in an untagged file."""
    out = []
    for instr in ops:
        if str(instr.operator) == "BDC" and len(instr.operands) == 2:
            props = instr.operands[1]
            if isinstance(props, Dictionary) and "/MCID" in props:
                rest = Dictionary({k: v for k, v in props.items() if k != "/MCID"})
                if len(rest):
                    out.append(pikepdf.ContentStreamInstruction([instr.operands[0], rest], Operator("BDC")))
                else:
                    out.append(pikepdf.ContentStreamInstruction([instr.operands[0]], Operator("BMC")))
                continue
        out.append(instr)
    return out


# ---------------------------------------------------------------------------
# Inline page-sized form XObjects
# ---------------------------------------------------------------------------

_RESOURCE_KINDS = ("/Font", "/XObject", "/ExtGState", "/ColorSpace", "/Pattern", "/Shading", "/Properties")


def _balanced(ops: list) -> bool:
    depth = 0
    for instr in ops:
        op = str(instr.operator)
        if op == "q":
            depth += 1
        elif op == "Q":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _merge_resources(page: pikepdf.Page, form: pikepdf.Object) -> bool:
    """Copy the form's resources into the page if no names clash."""
    form_res = form.get("/Resources")
    if form_res is None:
        return True
    page_res = page.obj.get("/Resources")
    if page_res is None:
        page.obj.Resources = Dictionary()
        page_res = page.obj.Resources
    for kind in _RESOURCE_KINDS:
        sub = form_res.get(kind)
        if sub is None:
            continue
        target = page_res.get(kind)
        for name, value in sub.items():
            if target is not None and name in target:
                existing = target[name]
                same = (existing.is_indirect and value.is_indirect and existing.objgen == value.objgen)
                if not same:
                    return False
    for kind in _RESOURCE_KINDS:
        sub = form_res.get(kind)
        if sub is None:
            continue
        if kind not in page_res:
            page_res[kind] = Dictionary()
        for name, value in sub.items():
            if name not in page_res[kind]:
                page_res[kind][name] = value
    return True


def inline_page_forms(pdf: pikepdf.Pdf) -> int:
    """Draw page-sized form XObjects that contain text directly in the page.

    Some print drivers and stamping tools wrap a whole page in one form
    XObject, which would otherwise become a single giant paragraph. Only done
    when it is safe: balanced graphics state, no transparency group or
    optional content, and no clashing resource names."""
    count = 0
    for page in pdf.pages:
        try:
            ops = pikepdf.parse_content_stream(page)
        except Exception:  # noqa: BLE001
            continue
        xobjects = page.obj.get("/Resources", Dictionary()).get("/XObject")
        if xobjects is None:
            continue
        box = page_box(page)
        page_area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        new_ops: list = []
        changed = False
        for instr in ops:
            if str(instr.operator) == "Do" and instr.operands:
                form = xobjects.get(str(instr.operands[0]))
                if (form is not None and form.get("/Subtype") == Name.Form and "/Group" not in form
                        and "/OC" not in form and "/StructParents" not in form):
                    try:
                        bb = [float(v) for v in form.BBox]
                        sub_ops = pikepdf.parse_content_stream(form)
                    except Exception:  # noqa: BLE001
                        sub_ops = None
                    if sub_ops is not None:
                        area = abs((bb[2] - bb[0]) * (bb[3] - bb[1]))
                        has_text = any(str(o.operator) == "BT" for o in sub_ops)
                        if area >= 0.5 * page_area and has_text and _balanced(sub_ops) and _merge_resources(page, form):
                            matrix = [float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                            new_ops.append(pikepdf.ContentStreamInstruction([], Operator("q")))
                            if matrix != [1, 0, 0, 1, 0, 0]:
                                new_ops.append(pikepdf.ContentStreamInstruction(matrix, Operator("cm")))
                            x0, y0, x1, y1 = min(bb[0], bb[2]), min(bb[1], bb[3]), max(bb[0], bb[2]), max(bb[1], bb[3])
                            new_ops.append(pikepdf.ContentStreamInstruction([x0, y0, x1 - x0, y1 - y0], Operator("re")))
                            new_ops.append(pikepdf.ContentStreamInstruction([], Operator("W")))
                            new_ops.append(pikepdf.ContentStreamInstruction([], Operator("n")))
                            new_ops.extend(sub_ops)
                            new_ops.append(pikepdf.ContentStreamInstruction([], Operator("Q")))
                            changed = True
                            count += 1
                            continue
            new_ops.append(instr)
        if changed:
            page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
    return count


# ---------------------------------------------------------------------------
# Building the tree
# ---------------------------------------------------------------------------


_LINK_SUBTYPES = {"/Link"}
_SKIP_ANNOTS = {"/Popup", "/PrinterMark", "/TrapNet"}


def _annot_rect(annot: pikepdf.Object) -> BBox | None:
    try:
        r = [float(v) for v in annot.Rect]
        return (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))
    except Exception:  # noqa: BLE001
        return None


def link_description(annot: pikepdf.Object, text: str = "") -> str:
    """A short plain-language description for a link annotation."""
    text = re.sub(r"\s+", " ", text).strip()
    if text and len(text) <= 120:
        return text
    action = annot.get("/A")
    if action is not None and action.get("/S") == Name("/URI"):
        uri = str(action.get("/URI", ""))
        if uri.lower().startswith("mailto:"):
            return "Email " + uri[7:]
        short = re.sub(r"^https?://(www\.)?", "", uri).rstrip("/")
        return ("Link to " + short)[:200] if short else "Link"
    if action is not None and action.get("/S") == Name("/GoTo") or "/Dest" in annot:
        return "Link to another part of this document"
    return "Link"


class _Builder:
    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.root = SE("Document")
        self.owner_of_item: dict[int, object] = {}
        self.mcid_counter: dict[int, int] = {}
        self.parent_arrays: dict[int, dict[int, SE]] = {}
        self.objrefs: list[tuple[pikepdf.Object, SE, int]] = []
        self.counts = {"elements": 1, "headings": 0, "figures": 0, "tables": 0, "lists": 0, "links": 0}

    # owner -> (tag, props)
    def tag_of(self, owner):
        if isinstance(owner, Artifact):
            if owner.kind in ("Header", "Footer"):
                return "/Artifact", Dictionary(Type=Name.Pagination, Subtype=Name("/" + owner.kind))
            if owner.kind == "Background":
                return "/Artifact", Dictionary(Type=Name.Layout)
            return "/Artifact", None
        if owner == EXISTING:
            return "/Artifact", None
        return "/" + owner.type, None

    def new_mcid(self, owner: SE, pos: tuple[int, int]) -> int:
        page = pos[0]
        mcid = self.mcid_counter.get(page, 0)
        self.mcid_counter[page] = mcid + 1
        self.parent_arrays.setdefault(page, {})[mcid] = owner
        owner.kids.append(MCRef(page, mcid, pos))
        if owner.page is None:
            owner.page = page
        return mcid

    def own(self, items: list[Item], owner) -> None:
        for it in items:
            self.owner_of_item[id(it)] = owner

    def add(self, parent: SE, child: SE) -> SE:
        parent.kids.append(child)
        self.counts["elements"] += 1
        return child

    def block_element(self, parent: SE, block: Block) -> SE | None:
        role = block.role
        if role == "Figure":
            se = self.add(parent, SE("Figure", page=block.page))
            se.attrs.append({"/O": Name.Layout, "/BBox": Array([round(v, 2) for v in block.bbox]),
                             "/Placement": Name.Block})
            self.own(block.items, se)
            self.counts["figures"] += 1
            return se
        if role == "Table" and block.grid is not None:
            table = self.add(parent, SE("Table", page=block.page))
            table.attrs.append({"/O": Name.Layout, "/BBox": Array([round(v, 2) for v in block.bbox]),
                                "/Placement": Name.Block})
            rows: dict[int, list] = {}
            for cell in block.grid.cells:
                rows.setdefault(cell.row, []).append(cell)
            for r in sorted(rows):
                tr = self.add(table, SE("TR", page=block.page))
                for cell in sorted(rows[r], key=lambda c: c.col):
                    header = r == 0 and len(rows) > 1
                    td = self.add(tr, SE("TH" if header else "TD", page=block.page))
                    attrs: dict = {"/O": Name.Table}
                    if header:
                        attrs["/Scope"] = Name.Column
                    if cell.row_span > 1:
                        attrs["/RowSpan"] = cell.row_span
                    if cell.col_span > 1:
                        attrs["/ColSpan"] = cell.col_span
                    if len(attrs) > 1:
                        td.attrs.append(attrs)
                    self.own(cell.items, td)
            self.counts["tables"] += 1
            return table
        if role == "Artifact":
            self.own(block.items, Artifact(block.artifact_kind))
            return None
        if role == "LI":
            raise AssertionError("list items are grouped by the caller")
        se = self.add(parent, SE(role if role in _BLOCK_TYPES else "P", page=block.page))
        if role.startswith("H"):
            self.counts["headings"] += 1
        self.own(block.items, se)
        return se

    def list_element(self, parent: SE, items: list[Block]) -> SE:
        lst = self.add(parent, SE("L", page=items[0].page))
        numbering = items[0].numbering or "None"
        lst.attrs.append({"/O": Name.List, "/ListNumbering": Name("/" + numbering)})
        for b in items:
            li = self.add(lst, SE("LI", page=b.page))
            if b.label_items:
                lbl = self.add(li, SE("Lbl", page=b.page))
                self.own(b.label_items, lbl)
            body = self.add(li, SE("LBody", page=b.page))
            labels = {id(x) for x in b.label_items}
            self.own([it for it in b.items if id(it) not in labels], body)
            for link in getattr(b, "links", []):
                _link_element(self, body, link, b.page)
        self.counts["lists"] += 1
        return lst


_BLOCK_TYPES = {"P", "H1", "H2", "H3", "H4", "H5", "H6", "Caption", "BlockQuote", "Note"}


def _group_lists(blocks: list[Block]) -> list[object]:
    """Group consecutive list items into lists."""
    out: list[object] = []
    current: list[Block] = []
    for b in blocks:
        if b.role == "LI":
            if current and abs(current[0].bbox[0] - b.bbox[0]) > max(b.size, 6.0) * 1.5:
                out.append(current)
                current = []
            current.append(b)
            continue
        if current:
            out.append(current)
            current = []
        out.append(b)
    if current:
        out.append(current)
    return out


def _serialize(pdf: pikepdf.Pdf, se: SE, parent_obj: pikepdf.Object, pages: list) -> pikepdf.Object:
    d = Dictionary(Type=Name.StructElem, S=Name("/" + se.type), P=parent_obj)
    obj = pdf.make_indirect(d)
    se.obj = obj
    if se.page is not None:
        obj.Pg = pages[se.page].obj
    if se.alt:
        obj.Alt = pikepdf.String(se.alt)
    if se.title:
        obj.T = pikepdf.String(se.title)
    if se.attrs:
        attrs = [Dictionary(a) for a in se.attrs]
        obj.A = attrs[0] if len(attrs) == 1 else Array(attrs)
    kids = sorted(se.kids, key=lambda k: k.first_pos() if isinstance(k, SE) else k.pos)
    arr = Array()
    for kid in kids:
        if isinstance(kid, SE):
            arr.append(_serialize(pdf, kid, obj, pages))
        elif isinstance(kid, MCRef):
            if kid.page == se.page:
                arr.append(kid.mcid)
            else:
                arr.append(Dictionary(Type=Name.MCR, Pg=pages[kid.page].obj, MCID=kid.mcid))
        elif isinstance(kid, ObjRef):
            arr.append(Dictionary(Type=Name.OBJR, Obj=kid.annot, Pg=pages[kid.page].obj))
    if len(arr) == 1 and not isinstance(kids[0], SE):
        obj.K = arr[0]
    else:
        obj.K = arr
    return obj


def is_tagged(pdf: pikepdf.Pdf) -> bool:
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return False
    k = root.get("/K")
    if k is None:
        return False
    if isinstance(k, Array) and len(k) == 0:
        return False
    return True


def autotag(pdf: pikepdf.Pdf, *, force: bool = False) -> TagResult:
    """Tag an untagged document in place. Returns what was created."""
    if is_tagged(pdf) and not force:
        return TagResult(False, len(pdf.pages), 0, 0, 0, 0, 0, 0, [], None, "Already tagged")
    inline_page_forms(pdf)
    fonts = FontCache()
    pages = list(pdf.pages)
    contents: list[PageContent] = []
    boxes: list[BBox] = []
    for page in pages:
        try:
            ops = _strip_mcids(pikepdf.parse_content_stream(page))
        except Exception:  # noqa: BLE001
            ops = []
        contents.append(analyze_page(page, fonts, ops))
        boxes.append(page_box(page))
    layouts = analyze_document(list(zip(contents, boxes)))
    builder = _Builder(pdf)
    title_guess = None

    for lay, content, page in zip(layouts, contents, pages):
        _attach_annotations(lay, content, page)

    for lay in layouts:
        for entry in _group_lists(lay.blocks):
            if isinstance(entry, list):
                builder.list_element(builder.root, entry)
                continue
            block: Block = entry
            if block.kind == "annot":
                _annot_element(builder, builder.root, block)
                continue
            se = builder.block_element(builder.root, block)
            if se is not None and title_guess is None and se.type in ("H1", "H2") and block.text:
                title_guess = block.text[:200]
            if se is not None:
                for link in getattr(block, "links", []):
                    _link_element(builder, se, link, lay.page)
        for art in lay.artifacts:
            builder.own(art.items, Artifact(art.artifact_kind))
        for it in lay.absorb:
            builder.owner_of_item[id(it)] = ABSORB

    # Rewrite content streams
    for index, (page, content) in enumerate(zip(pages, contents)):
        leaf_of: dict[int, tuple[int, object]] = {}
        for it in content.items:
            if it.artifact:
                owner = None  # already inside an existing /Artifact sequence
            else:
                owner = builder.owner_of_item.get(id(it), Artifact())
            leaf_of[it.start] = (it.end, owner)
        if not content.ops:
            continue
        emitter = _Emitter(content.ops, index, builder.tag_of, builder.new_mcid)
        tree = _build_tree(content.ops, leaf_of)
        if tree is not None:
            emitter._emit_children(tree.children)
        else:
            emitter.emit_leafwise(leaf_of)
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(emitter.out))

    # Serialize
    struct_root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc_obj = _serialize(pdf, builder.root, struct_root, pages)
    struct_root.K = doc_obj
    nums = Array()
    next_key = 0
    for index, page in enumerate(pages):
        if index in builder.parent_arrays:
            mapping = builder.parent_arrays[index]
            arr = Array([mapping[m].obj if m in mapping else None for m in range(max(mapping) + 1)])
            page.obj.StructParents = next_key
            nums.append(next_key)
            nums.append(pdf.make_indirect(arr))
            next_key += 1
        elif "/StructParents" in page.obj:
            del page.obj["/StructParents"]
    for annot, se, _page in builder.objrefs:
        annot.StructParent = next_key
        nums.append(next_key)
        nums.append(se.obj)
        next_key += 1
    struct_root.ParentTree = pdf.make_indirect(Dictionary(Nums=nums))
    struct_root.ParentTreeNextKey = next_key
    pdf.Root.StructTreeRoot = struct_root
    mark = pdf.Root.get("/MarkInfo")
    if mark is None:
        pdf.Root.MarkInfo = Dictionary(Marked=True)
    else:
        mark.Marked = True
    for page in pages:
        annots = page.obj.get("/Annots")
        if annots is not None and len(annots):
            page.obj.Tabs = Name.S
    c = builder.counts
    return TagResult(True, len(pages), c["elements"], c["headings"], c["figures"], c["tables"], c["lists"],
                     c["links"], [lay.order_method for lay in layouts], title_guess)


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------


def _attach_annotations(lay: PageLayout, content: PageContent, page: pikepdf.Page) -> None:
    annots = page.obj.get("/Annots")
    if annots is None:
        return
    for annot in annots:
        try:
            subtype = str(annot.get("/Subtype", ""))
        except Exception:  # noqa: BLE001
            continue
        if subtype in _SKIP_ANNOTS or not annot.is_indirect:
            continue
        flags = int(annot.get("/F", 0) or 0)
        if flags & 2:  # hidden
            continue
        rect = _annot_rect(annot)
        if rect is None:
            continue
        if subtype in _LINK_SUBTYPES:
            host = _best_text_block(lay.blocks, rect)
            if host is not None:
                inside = [it for it in host.items if it.kind == "text" and
                          rect[0] - 1 <= it.cx <= rect[2] + 1 and rect[1] - 1 <= it.cy <= rect[3] + 1]
                if not hasattr(host, "links"):
                    host.links = []  # type: ignore[attr-defined]
                host.links.append((annot, inside))  # type: ignore[attr-defined]
                continue
        kind = "Form" if subtype == "/Widget" else ("Link" if subtype == "/Link" else "Annot")
        block = Block("annot", lay.page, rect, role=kind)
        block.annot = annot
        insert_by_position(lay.blocks, block)


def _best_text_block(blocks: list[Block], rect: BBox) -> Block | None:
    best, best_area = None, 0.0
    for b in blocks:
        if b.kind not in ("text", "form_text") or b.role == "Artifact":
            continue
        ox = min(b.bbox[2], rect[2]) - max(b.bbox[0], rect[0])
        oy = min(b.bbox[3], rect[3]) - max(b.bbox[1], rect[1])
        if ox > 0 and oy > 0 and ox * oy > best_area:
            best, best_area = b, ox * oy
    return best


def _link_element(builder: _Builder, host: SE, link, page_index: int) -> None:
    annot, inside = link
    # Inside a list item the text belongs to LBody
    target = host
    if host.type in ("L",):
        return
    se = builder.add(target, SE("Link", page=page_index))
    text = " ".join(it.text for it in inside)
    builder.own(inside, se)
    if "/Contents" not in annot or not str(annot.get("/Contents", "")).strip():
        annot.Contents = pikepdf.String(link_description(annot, text))
    se.kids.append(ObjRef(page_index, annot, (page_index, min((it.start for it in inside), default=10**8))))
    builder.objrefs.append((annot, se, page_index))
    builder.counts["links"] += 1


def _annot_element(builder: _Builder, parent: SE, block: Block) -> None:
    annot = block.annot
    se = builder.add(parent, SE(block.role, page=block.page))
    if block.role == "Link" and ("/Contents" not in annot or not str(annot.get("/Contents", "")).strip()):
        annot.Contents = pikepdf.String(link_description(annot))
        builder.counts["links"] += 1
    if block.role == "Annot" and "/Contents" in annot:
        se.alt = str(annot.Contents)[:500]
    se.kids.append(ObjRef(block.page, annot, (block.page, 10**8)))
    builder.objrefs.append((annot, se, block.page))
