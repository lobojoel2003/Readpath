# SPDX-License-Identifier: Apache-2.0
"""Read an existing structure tree (tags) in a convenient form."""

from __future__ import annotations

from dataclasses import dataclass, field

import pikepdf
from pikepdf import Array, Dictionary, Name

STANDARD_TYPES = {
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption", "TOC", "TOCI", "Index",
    "NonStruct", "Private", "P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LI", "Lbl", "LBody",
    "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot", "Span", "Quote", "Note", "Reference",
    "BibEntry", "Code", "Link", "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP", "Figure",
    "Formula", "Form",
    # PDF 2.0
    "DocumentFragment", "Aside", "Title", "FENote", "Sub", "Em", "Strong", "Artifact",
}
# Containers we look through when listing blocks in reading order
CONTAINERS = {"Document", "DocumentFragment", "Part", "Art", "Sect", "Div", "NonStruct", "Private",
              "Aside", "THead", "TBody", "TFoot"}

LABELS = {
    "P": "Paragraph", "H": "Heading", "H1": "Heading 1", "H2": "Heading 2", "H3": "Heading 3",
    "H4": "Heading 4", "H5": "Heading 5", "H6": "Heading 6", "Figure": "Image", "Table": "Table",
    "L": "List", "LI": "List item", "Caption": "Caption", "BlockQuote": "Quote", "Note": "Note",
    "TOC": "Table of contents", "Form": "Form field", "Link": "Link", "Formula": "Formula",
    "Annot": "Comment", "Title": "Title", "Span": "Text", "Code": "Code", "Quote": "Quote",
    "Index": "Index", "Reference": "Reference", "BibEntry": "Bibliography entry", "Aside": "Aside",
    "TOCI": "Contents item", "Lbl": "Label", "LBody": "List text",
}


def label_for(std_type: str) -> str:
    return LABELS.get(std_type, std_type)


@dataclass(eq=False)
class Kid:
    kind: str  # "mcid" | "objr" | "elem"
    page: pikepdf.Object | None = None
    mcid: int | None = None
    stm: pikepdf.Object | None = None
    obj: pikepdf.Object | None = None  # OBJR target or element dict


@dataclass(eq=False)
class Elem:
    obj: pikepdf.Object
    type: str  # raw /S without slash
    std: str  # role-mapped standard type
    parent: "Elem | None"
    page: pikepdf.Object | None
    kids: list = field(default_factory=list)  # Kid (mcid/objr) or Elem
    index: int = 0  # pre-order position, used as an id

    @property
    def alt(self) -> str | None:
        v = self.obj.get("/Alt")
        return str(v) if v is not None else None

    @property
    def actual_text(self) -> str | None:
        v = self.obj.get("/ActualText")
        return str(v) if v is not None else None

    def elements(self) -> list["Elem"]:
        return [k for k in self.kids if isinstance(k, Elem)]

    def walk(self):
        yield self
        for k in self.kids:
            if isinstance(k, Elem):
                yield from k.walk()

    def content_refs(self) -> list[Kid]:
        """All marked-content references in this subtree, in order."""
        out: list[Kid] = []
        for k in self.kids:
            if isinstance(k, Elem):
                out.extend(k.content_refs())
            elif k.kind == "mcid":
                out.append(k)
        return out

    def attribute(self, key: str):
        attrs = self.obj.get("/A")
        if attrs is None:
            return None
        items = attrs if isinstance(attrs, Array) else [attrs]
        for a in items:
            if isinstance(a, Dictionary) and key in a:
                return a[key]
        return None


@dataclass
class StructTree:
    root: pikepdf.Object | None
    top: list[Elem]
    elements: list[Elem]
    role_map: dict[str, str]
    unknown_types: set[str]

    @property
    def empty(self) -> bool:
        return not self.elements


def _resolve_role(t: str, role_map: dict[str, str]) -> str:
    seen = set()
    while t not in STANDARD_TYPES and t in role_map and t not in seen:
        seen.add(t)
        t = role_map[t]
    return t


def _name(v) -> str:
    s = str(v)
    return s[1:] if s.startswith("/") else s


def read_tree(pdf: pikepdf.Pdf, max_elements: int = 200000) -> StructTree:
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return StructTree(None, [], [], {}, set())
    role_map: dict[str, str] = {}
    rm = root.get("/RoleMap")
    if isinstance(rm, Dictionary):
        for k, v in rm.items():
            role_map[_name(k)] = _name(v)
    elements: list[Elem] = []
    unknown: set[str] = set()
    seen: set[tuple[int, int]] = set()

    def visit(obj, parent: Elem | None, page) -> Elem | None:
        if len(elements) >= max_elements:
            return None
        if obj.is_indirect:
            if obj.objgen in seen:
                return None
            seen.add(obj.objgen)
        raw = _name(obj.get("/S", "/NonStruct"))
        std = _resolve_role(raw, role_map)
        if std not in STANDARD_TYPES:
            unknown.add(raw)
        pg = obj.get("/Pg", page)
        el = Elem(obj, raw, std, parent, pg, index=len(elements))
        elements.append(el)
        k = obj.get("/K")
        kids = k if isinstance(k, Array) else ([] if k is None else [k])
        for kid in kids:
            if isinstance(kid, int) or (isinstance(kid, pikepdf.Object) and not isinstance(kid, Dictionary)
                                        and _is_int(kid)):
                el.kids.append(Kid("mcid", pg, int(kid)))
            elif isinstance(kid, Dictionary):
                t = kid.get("/Type")
                if t == Name.MCR:
                    el.kids.append(Kid("mcid", kid.get("/Pg", pg), int(kid.get("/MCID", -1)), kid.get("/Stm")))
                elif t == Name.OBJR:
                    el.kids.append(Kid("objr", kid.get("/Pg", pg), obj=kid.get("/Obj")))
                elif "/S" in kid:
                    child = visit(kid, el, pg)
                    if child is not None:
                        el.kids.append(child)
                elif "/MCID" in kid:
                    el.kids.append(Kid("mcid", kid.get("/Pg", pg), int(kid.get("/MCID", -1)), kid.get("/Stm")))
        return el

    top: list[Elem] = []
    k = root.get("/K")
    kids = k if isinstance(k, Array) else ([] if k is None else [k])
    for kid in kids:
        if isinstance(kid, Dictionary) and "/S" in kid:
            el = visit(kid, None, None)
            if el is not None:
                top.append(el)
    return StructTree(root, top, elements, role_map, unknown)


def _is_int(v) -> bool:
    try:
        int(v)
        return not isinstance(v, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Name, pikepdf.String))
    except (TypeError, ValueError):
        return False


def blocks_in_order(tree: StructTree) -> list[Elem]:
    """Block-level elements in reading order, looking through containers."""
    out: list[Elem] = []

    def visit(el: Elem) -> None:
        if el.std in CONTAINERS:
            for k in el.kids:
                if isinstance(k, Elem):
                    visit(k)
            return
        out.append(el)

    for el in tree.top:
        visit(el)
    return out


def page_index_map(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    return {p.obj.objgen: i for i, p in enumerate(pdf.pages)}


def elem_pages(el: Elem, pages: dict[tuple[int, int], int]) -> list[int]:
    found: list[int] = []
    for ref in el.content_refs():
        if ref.page is not None and ref.page.objgen in pages:
            i = pages[ref.page.objgen]
            if i not in found:
                found.append(i)
    for e in el.walk():
        for k in e.kids:
            if isinstance(k, Kid) and k.kind == "objr" and k.page is not None and k.page.objgen in pages:
                i = pages[k.page.objgen]
                if i not in found:
                    found.append(i)
    if not found and el.page is not None and el.page.objgen in pages:
        found.append(pages[el.page.objgen])
    return found
