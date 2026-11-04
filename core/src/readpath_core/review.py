# SPDX-License-Identifier: Apache-2.0
"""Guided review: the edits a person makes that software can't decide alone.

Works on any tagged PDF (tagged by its author or by Readpath):

* list the blocks on a page in reading order, reorder them, change their type
  (paragraph, heading level, caption) or take them out of the reading order;
* describe images (alt text) or mark them decorative;
* mark table header rows/columns;
* set the title and language, and record which steps a person confirmed.

Element ids are ``e<n>``, the element's position in a pre-order walk of the
tree. They are only valid until the next edit; every edit re-reads the tree.
"""

from __future__ import annotations

import re

import pikepdf
from pikepdf import Array, Dictionary, Name

from . import metadata as md
from .content import BBox, PageContent, analyze_page, page_box, page_rotation, to_view_fraction, union
from .fonts import FontCache
from .lang import detect_language, is_valid_lang
from .structure import Elem, Kid, StructTree, blocks_in_order, elem_pages, label_for, read_tree

EDITABLE_TYPES = {"P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "Caption", "BlockQuote", "Note", "Span",
                  "Title", "Div", "NonStruct"}
SETTABLE_TYPES = {"P", "H1", "H2", "H3", "H4", "H5", "H6", "Caption", "Artifact"}


class ReviewError(ValueError):
    """An edit that can't be applied (message is shown to the person)."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _k_array(obj: pikepdf.Object) -> Array:
    k = obj.get("/K")
    if isinstance(k, Array):
        return k
    arr = Array([] if k is None else [k])
    obj.K = arr
    return obj.K


class Review:
    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.fonts = FontCache()
        self._contents: dict[int, PageContent] = {}
        self.refresh()

    # -- reading helpers -------------------------------------------------
    def refresh(self) -> None:
        self.tree: StructTree = read_tree(self.pdf)
        self.page_map = {p.obj.objgen: i for i, p in enumerate(self.pdf.pages)}

    def content(self, page_index: int) -> PageContent:
        if page_index not in self._contents:
            self._contents[page_index] = analyze_page(self.pdf.pages[page_index], self.fonts)
        return self._contents[page_index]

    def _forget(self, page_index: int) -> None:
        self._contents.pop(page_index, None)

    def element(self, elem_id: str) -> Elem:
        m = re.fullmatch(r"e(\d+)", elem_id or "")
        if not m or int(m.group(1)) >= len(self.tree.elements):
            raise ReviewError("That item no longer exists. Reload to see the latest version.")
        return self.tree.elements[int(m.group(1))]

    def elem_id(self, el: Elem) -> str:
        return f"e{el.index}"

    def _ref_page(self, ref: Kid) -> int | None:
        if ref.page is None:
            return None
        return self.page_map.get(ref.page.objgen)

    def elem_text(self, el: Elem, limit: int = 2000) -> str:
        if el.actual_text:
            return _norm(el.actual_text)[:limit]
        parts: list[str] = []
        total = 0
        for ref in el.content_refs():
            p = self._ref_page(ref)
            if p is None:
                continue
            pc = self.content(p)
            if ref.stm is not None:
                info = pc.form_mcids.get((ref.stm.objgen, ref.mcid)) if ref.stm.is_indirect else None
            else:
                info = pc.mcids.get(ref.mcid)
            if info and info.text:
                parts.append(info.text)
                total += len(info.text)
                if total > limit:
                    break
        return _norm(" ".join(parts))[:limit]

    def elem_bbox(self, el: Elem, page_index: int) -> BBox | None:
        box = None
        for ref in el.content_refs():
            if self._ref_page(ref) != page_index:
                continue
            pc = self.content(page_index)
            if ref.stm is not None:
                info = pc.form_mcids.get((ref.stm.objgen, ref.mcid)) if ref.stm.is_indirect else None
            else:
                info = pc.mcids.get(ref.mcid)
            if info and info.bbox:
                box = union(box, info.bbox)
        for e in el.walk():
            for k in e.kids:
                if isinstance(k, Kid) and k.kind == "objr" and k.obj is not None and self._ref_page(k) == page_index:
                    try:
                        r = [float(v) for v in k.obj.Rect]
                        box = union(box, (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])))
                    except Exception:  # noqa: BLE001
                        pass
        if box is None:
            bb = el.attribute("/BBox")
            if bb is not None and len(bb) == 4:
                box = tuple(float(v) for v in bb)  # type: ignore[assignment]
        return box

    def view_bbox(self, el: Elem, page_index: int):
        box = self.elem_bbox(el, page_index)
        if box is None:
            return None
        page = self.pdf.pages[page_index]
        return list(to_view_fraction(box, page_box(page), page_rotation(page)))

    def pages(self, el: Elem) -> list[int]:
        return elem_pages(el, self.page_map)

    # -- overview --------------------------------------------------------
    def page_sizes(self) -> list[dict]:
        out = []
        for i, page in enumerate(self.pdf.pages):
            box = page_box(page)
            w, h = box[2] - box[0], box[3] - box[1]
            if page_rotation(page) in (90, 270):
                w, h = h, w
            out.append({"number": i + 1, "width": round(w, 2), "height": round(h, 2)})
        return out

    def suggested_title(self, filename: str | None = None) -> str | None:
        for el in self.tree.elements:
            if el.std in ("Title", "H1", "H"):
                text = self.elem_text(el, 300)
                if len(text) >= 3:
                    return text[:200]
        for el in self.tree.elements:
            if el.std in ("H2",):
                text = self.elem_text(el, 300)
                if len(text) >= 3:
                    return text[:200]
        # Largest text on the first page
        if len(self.pdf.pages):
            pc = self.content(0)
            texts = [it for it in pc.items if it.kind == "text" and len(it.text.strip()) >= 3 and not it.artifact]
            if texts:
                biggest = max(it.size for it in texts)
                line = " ".join(it.text.strip() for it in texts if abs(it.size - biggest) < 0.5)
                if len(line) >= 3:
                    return _norm(line)[:200]
        return md.title_from_filename(filename)

    def suggested_lang(self) -> str | None:
        sample = []
        for i in range(min(5, len(self.pdf.pages))):
            sample.extend(it.text for it in self.content(i).items if it.kind in ("text", "form"))
        lang, _ = detect_language(" ".join(sample))
        return lang

    def overview(self, filename: str | None = None) -> dict:
        reviewed = md.reviewed_steps(self.pdf)
        figures = [e for e in self.tree.elements if e.std == "Figure"]
        tables = [e for e in self.tree.elements if e.std == "Table"]
        title = md.get_title(self.pdf)
        lang = md.get_lang(self.pdf)
        blocks = blocks_in_order(self.tree)
        per_page = [0] * len(self.pdf.pages)
        for b in blocks:
            for p in self.pages(b):
                per_page[p] += 1
        missing_alt = sum(1 for f in figures if not (f.alt or "").strip() and not (f.actual_text or "").strip())

        def status(step: str, count: int) -> str:
            if step in ("images", "tables") and count == 0:
                return "not_needed"
            if step == "images" and missing_alt == 0 and figures and "images" not in reviewed:
                return "done" if not md.autotagged(self.pdf) else "todo"
            return "done" if step in reviewed else "todo"

        steps = [
            {"id": "basics", "label": "Title and language", "count": 2,
             "status": "done" if "basics" in reviewed or (
                 title and md.is_meaningful_title(title) and not md.title_guessed(self.pdf)
                 and lang and is_valid_lang(lang) and not md.autotagged(self.pdf)) else "todo"},
            {"id": "reading-order", "label": "Reading order", "count": len(self.pdf.pages),
             "status": status("reading-order", len(self.pdf.pages))},
            {"id": "images", "label": "Images", "count": len(figures), "status": status("images", len(figures))},
            {"id": "tables", "label": "Tables", "count": len(tables), "status": status("tables", len(tables))},
        ]
        pages = self.page_sizes()
        for p, count in zip(pages, per_page):
            p["blocks"] = count
        return {
            "tagged": not self.tree.empty,
            "page_count": len(self.pdf.pages),
            "title": title,
            "lang": lang,
            "suggested_title": self.suggested_title(filename),
            "suggested_lang": self.suggested_lang() if not lang else None,
            "steps": steps,
            "pages": pages,
        }

    # -- reading order ---------------------------------------------------
    def _page_blocks(self, page_index: int) -> list[Elem]:
        return [b for b in blocks_in_order(self.tree) if page_index in self.pages(b)]

    def page_blocks(self, page_index: int) -> list[dict]:
        self._check_page(page_index)
        out = []
        for el in self._page_blocks(page_index):
            if el.std == "Figure":
                text = (el.alt or el.actual_text or "").strip() or "(no description yet)"
            elif el.std == "Table":
                text = self.elem_text(el, 400)
            else:
                text = self.elem_text(el, 400)
            out.append({
                "id": self.elem_id(el),
                "type": el.std,
                "label": label_for(el.std),
                "text": text[:160] + ("…" if len(text) > 160 else ""),
                "bbox": self.view_bbox(el, page_index),
                "editable_type": el.std in EDITABLE_TYPES,
                "pages": [p + 1 for p in self.pages(el)],
            })
        return out

    def _check_page(self, page_index: int) -> None:
        if not 0 <= page_index < len(self.pdf.pages):
            raise ReviewError("That page doesn't exist.")

    def _slot(self, el: Elem) -> tuple[pikepdf.Object, int]:
        parent_obj = el.parent.obj if el.parent is not None else self.tree.root
        arr = _k_array(parent_obj)
        for i, kid in enumerate(arr):
            if isinstance(kid, Dictionary) and kid.is_indirect and el.obj.is_indirect and kid.objgen == el.obj.objgen:
                return parent_obj, i
            if kid.same_owner_as(el.obj) and kid == el.obj:  # direct objects
                return parent_obj, i
        raise ReviewError("The tags in this file are unusual; this item can't be moved.")

    def reorder_page(self, page_index: int, ids: list[str]) -> None:
        self._check_page(page_index)
        blocks = self._page_blocks(page_index)
        current = [self.elem_id(b) for b in blocks]
        if sorted(current) != sorted(ids) or len(set(ids)) != len(ids):
            raise ReviewError("The list of blocks changed. Reload and try again.")
        if current == list(ids):
            return
        by_id = {self.elem_id(b): b for b in blocks}
        slots = [self._slot(b) for b in blocks]
        moved = [by_id[i] for i in ids]
        for (parent_obj, idx), el in zip(slots, moved):
            parent_obj.K[idx] = el.obj
            if parent_obj is not None:
                el.obj.P = parent_obj
        self.refresh()

    def set_block_type(self, elem_id: str, new_type: str) -> None:
        el = self.element(elem_id)
        if new_type not in SETTABLE_TYPES:
            raise ReviewError(f"“{new_type}” is not a type you can choose here.")
        if new_type == "Artifact":
            self.make_artifact(el)
            return
        if el.std not in EDITABLE_TYPES:
            raise ReviewError(f"A {label_for(el.std).lower()} can't be changed to another type.")
        el.obj.S = Name("/" + new_type)
        self.refresh()

    # -- artifacts -------------------------------------------------------
    def make_artifact(self, el: Elem) -> None:
        """Take an element out of the tags and mark its content as decoration."""
        for e in el.walk():
            for k in e.kids:
                if isinstance(k, Kid) and k.kind == "objr":
                    raise ReviewError("This block contains a link or form field, so it has to stay in the "
                                      "reading order.")
        refs = el.content_refs()
        by_page: dict[int, set[int]] = {}
        for ref in refs:
            p = self._ref_page(ref)
            if p is None or ref.stm is not None:
                continue
            by_page.setdefault(p, set()).add(ref.mcid)
        for p, mcids in by_page.items():
            self._artifact_mcids(p, mcids)
        parent_obj, idx = self._slot(el)
        del parent_obj.K[idx]
        self.refresh()

    def _artifact_mcids(self, page_index: int, mcids: set[int]) -> None:
        page = self.pdf.pages[page_index]
        ops = pikepdf.parse_content_stream(page)
        out = []
        for instr in ops:
            if str(instr.operator) == "BDC" and len(instr.operands) == 2:
                props = instr.operands[1]
                if isinstance(props, Dictionary) and "/MCID" in props and int(props.MCID) in mcids:
                    out.append(pikepdf.ContentStreamInstruction([Name.Artifact], pikepdf.Operator("BMC")))
                    continue
            out.append(instr)
        page.obj.Contents = self.pdf.make_stream(pikepdf.unparse_content_stream(out))
        key = page.obj.get("/StructParents")
        root = self.tree.root
        if key is not None and root is not None and "/ParentTree" in root:
            nt = pikepdf.NumberTree(root.ParentTree)
            arr = nt.get(int(key))
            if isinstance(arr, Array):
                for m in mcids:
                    if 0 <= m < len(arr):
                        arr[m] = None
        self._forget(page_index)

    # -- images ------------------------------------------------------------
    def figures(self) -> list[dict]:
        blocks = blocks_in_order(self.tree)
        out = []
        for el in self.tree.elements:
            if el.std != "Figure":
                continue
            pages = self.pages(el)
            page = pages[0] if pages else 0
            out.append({
                "id": self.elem_id(el),
                "page": page + 1,
                "bbox": self.view_bbox(el, page) if pages else None,
                "alt": (el.alt or el.actual_text or None),
                "context": self._context(el, blocks),
            })
        return out

    def _context(self, el: Elem, blocks: list[Elem]) -> str:
        top = el
        while top is not None and top not in blocks:
            top = top.parent  # type: ignore[assignment]
        if top is None:
            return ""
        i = blocks.index(top)
        before = [self.elem_text(b, 300) for b in blocks[max(0, i - 2) : i] if b.std != "Figure"]
        after = [self.elem_text(b, 300) for b in blocks[i + 1 : i + 3] if b.std != "Figure"]
        text = " … ".join(t for t in before + after if t)
        return text[:500]

    def set_figure(self, elem_id: str, alt: str | None = None, decorative: bool = False) -> None:
        el = self.element(elem_id)
        if el.std != "Figure":
            raise ReviewError("That item is not an image.")
        if decorative:
            self.make_artifact(el)
            return
        alt = _norm(alt or "")
        if not alt:
            raise ReviewError("Write a short description, or mark the image as decorative.")
        el.obj.Alt = pikepdf.String(alt[:2000])
        self.refresh()

    # -- tables ------------------------------------------------------------
    def _rows(self, table: Elem) -> list[Elem]:
        rows: list[Elem] = []

        def visit(e: Elem) -> None:
            for k in e.elements():
                if k.std == "TR":
                    rows.append(k)
                elif k.std in ("THead", "TBody", "TFoot"):
                    visit(k)

        visit(table)
        return rows

    def tables(self) -> list[dict]:
        out = []
        for el in self.tree.elements:
            if el.std != "Table":
                continue
            pages = self.pages(el)
            page = pages[0] if pages else 0
            rows = []
            for tr in self._rows(el):
                cells = []
                for cell in tr.elements():
                    if cell.std not in ("TH", "TD"):
                        continue
                    rs = cell.attribute("/RowSpan")
                    cs = cell.attribute("/ColSpan")
                    cells.append({
                        "text": self.elem_text(cell, 200),
                        "header": cell.std == "TH",
                        "row_span": int(rs) if rs is not None else 1,
                        "col_span": int(cs) if cs is not None else 1,
                    })
                rows.append(cells)
            header_row = bool(rows) and bool(rows[0]) and all(c["header"] for c in rows[0])
            header_col = bool(rows) and all(r and r[0]["header"] for r in rows)
            out.append({
                "id": self.elem_id(el),
                "page": page + 1,
                "bbox": self.view_bbox(el, page) if pages else None,
                "rows": rows,
                "header_row": header_row,
                "header_column": header_col,
            })
        return out

    def set_table_headers(self, elem_id: str, header_row: bool, header_column: bool) -> None:
        el = self.element(elem_id)
        if el.std != "Table":
            raise ReviewError("That item is not a table.")
        rows = self._rows(el)
        if not rows:
            raise ReviewError("This table has no rows Readpath can edit.")
        for r, tr in enumerate(rows):
            cells = [c for c in tr.elements() if c.std in ("TH", "TD")]
            for c, cell in enumerate(cells):
                is_head = (r == 0 and header_row) or (c == 0 and header_column)
                scope = None
                if is_head:
                    if r == 0 and header_row and c == 0 and header_column:
                        scope = Name.Both
                    elif r == 0 and header_row:
                        scope = Name.Column
                    else:
                        scope = Name.Row
                cell.obj.S = Name.TH if is_head else Name.TD
                _set_scope(cell.obj, scope)
        self.refresh()

    # -- metadata ----------------------------------------------------------
    def set_metadata(self, title: str | None, lang: str | None) -> None:
        if title is not None:
            title = _norm(title)
            if len(title) < 2:
                raise ReviewError("Enter a title that describes the document.")
            md.set_title(self.pdf, title, guessed=False)
            md.set_display_title(self.pdf)
        if lang is not None:
            lang = lang.strip()
            if not is_valid_lang(lang):
                raise ReviewError(f"“{lang}” is not a language code. Use a code like en-US or es.")
            md.set_lang(self.pdf, lang)

    def complete_step(self, step: str) -> None:
        if step not in md.REVIEW_STEPS:
            raise ReviewError("Unknown review step.")
        md.mark_reviewed(self.pdf, step)


def _set_scope(cell: pikepdf.Object, scope) -> None:
    attrs = cell.get("/A")
    items = list(attrs) if isinstance(attrs, Array) else ([attrs] if attrs is not None else [])
    table_attr = None
    for a in items:
        if isinstance(a, Dictionary) and a.get("/O") == Name.Table:
            table_attr = a
            break
    if scope is None:
        if table_attr is not None and "/Scope" in table_attr:
            del table_attr["/Scope"]
        return
    if table_attr is None:
        table_attr = Dictionary(O=Name.Table)
        items.append(table_attr)
        cell.A = items[0] if len(items) == 1 else Array(items)
    table_attr.Scope = scope
