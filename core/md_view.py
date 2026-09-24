# -*- coding: utf-8 -*-
"""Markdown → tk.Text 渲染

语法解析交给 Python-Markdown（BSD-3，纯 Python），本模块只负责把它产出的 HTML
映射成 tk.Text 的 tag 样式，因此不引入带二进制的 GUI 依赖，配色也能跟随主题。

依赖缺失时 MD_AVAILABLE 为 False，调用方自行退回原文视图。
"""

import webbrowser

from tkinter import font as _tkfont

try:
    import markdown
    from markdown.extensions.fenced_code import FencedCodeExtension
    from markdown.extensions.tables import TableExtension
    from lxml import html as _lhtml
    MD_AVAILABLE = True
except Exception:
    MD_AVAILABLE = False

# 行内遍历时交还给 block() 处理的元素；<p>/<hN> 不在其中，
# 以便宽松列表和引用块里包了一层的段落仍能画出来
_BLOCK_TAGS = ("ul", "ol", "table", "pre", "blockquote", "hr", "div")
_HEAD_RAISE = {1: 6, 2: 4, 3: 2, 4: 1, 5: 0, 6: 0}
_TABLE_MAX_PX = 330
_INDENT = 22


def _tag_of(node):
    return node.tag if isinstance(node.tag, str) else ""


def _plain(node):
    return " ".join("".join(node.itertext()).split())


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class _Renderer:
    def __init__(self, widget, pal, ui_font, mono_font, base):
        self.w = widget
        self.pal = pal
        self.ui_font = ui_font
        self.mono_font = mono_font
        self.base = base
        self.tags = set()
        self._font = None

    # ── tag 工具：同一样式只配置一次 ─────────────────────
    def _tag(self, prefix, **opts):
        name = "%s|%s" % (prefix, ",".join("%s=%r" % kv for kv in sorted(opts.items())))
        if name not in self.tags:
            self.tags.add(name)
            self.w.tag_configure(name, **opts)
        return name

    def _pos(self):
        return self.w.index("end-1c")

    def _mfont(self):
        if self._font is None:
            self._font = getattr(self.w, "_md_view_mfont", None)
            if self._font is None:
                self._font = _tkfont.Font(font=(self.mono_font, self.base - 1), widget=self.w)
                self.w._md_view_mfont = self._font
        return self._font

    def _span(self, start, tag):
        self.w.tag_add(tag, start, self._pos())

    def _fg(self, key, fallback):
        return self.pal.get(key, fallback)

    # ── 行内 ─────────────────────────────────────────────
    def _ctx(self, **over):
        ctx = {"size": self.base, "fg": self._fg("text", "#000000")}
        ctx.update(over)
        return ctx

    def _inline_tag(self, ctx):
        style = " ".join(w for w in ("bold" if ctx.get("bold") else "",
                                     "italic" if ctx.get("italic") else "")).strip()
        opts = {
            "font": (self.mono_font if ctx.get("mono") else self.ui_font,
                     ctx.get("size", self.base), style or "roman"),
            "foreground": ctx["fg"],
        }
        if ctx.get("hilite"):
            opts["background"] = self._fg("elevated", "#f8f8f8")
        if ctx.get("strike"):
            opts["overstrike"] = True
        return self._tag("s", **opts)

    def _link(self, href):
        name = "link|%s" % href
        if name not in self.tags:
            self.tags.add(name)
            self.w.tag_configure(name, foreground=self._fg("primary", "#007acc"), underline=True)
            self.w.tag_bind(name, "<Enter>", lambda _e: self.w.configure(cursor="hand2"))
            self.w.tag_bind(name, "<Leave>", lambda _e: self.w.configure(cursor=""))
        return name

    def _write(self, text, ctx):
        if not text:
            return
        if ctx.get("quote"):
            text = text.replace("\n", "\n▏ ")
        tags = [self._link(ctx["href"])] if ctx.get("href") else []
        tags.append(self._inline_tag(ctx))
        self.w.insert("end", text.replace("\xa0", " "), tags)

    def _inline(self, node, ctx):
        self._write(node.text, ctx)
        for child in node:
            tag = _tag_of(child)
            if tag in _BLOCK_TAGS:
                self._write(child.tail, ctx)
                continue
            if tag == "br":
                self.w.insert("end", "\n")
                self._write(child.tail, ctx)
                continue
            if tag == "img":
                alt = (child.get("alt") or "").strip()
                if alt:
                    self.w.insert("end", "🖼 " + alt, self._inline_tag(ctx))
                self._write(child.tail, ctx)
                continue
            sub = dict(ctx)
            if tag in ("strong", "b"):
                sub["bold"] = True
            elif tag in ("em", "i"):
                sub["italic"] = True
            elif tag == "code":
                sub["mono"] = sub["hilite"] = True
            elif tag in ("del", "s", "strike"):
                sub["strike"] = True
            elif tag == "a":
                sub["href"] = child.get("href") or ""
            self._inline(child, sub)
            self._write(child.tail, ctx)

    # ── 块级 ─────────────────────────────────────────────
    def block(self, node, depth=0):
        tag = _tag_of(node)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading(node, int(tag[1]))
        elif tag == "p":
            self._para(node, depth)
        elif tag in ("ul", "ol"):
            self._list(node, depth, tag == "ol")
        elif tag == "blockquote":
            self._quote(node, depth)
        elif tag == "pre":
            self._pre(node)
        elif tag == "hr":
            self._rule()
        elif tag == "table":
            self._table(node)
        elif tag in ("div", "body", ""):
            for child in node:
                self.block(child, depth)
        elif _plain(node):
            self._para(node, depth)

    def _heading(self, node, level):
        start = self._pos()
        self._inline(node, self._ctx(size=self.base + _HEAD_RAISE[level], bold=True,
                                     fg=self._fg("primary", "#000000") if level <= 2
                                     else self._fg("text", "#000000")))
        self.w.insert("end", "\n")
        self._span(start, self._tag("h", spacing1=16 if level <= 2 else 10,
                                    spacing3=4, rmargin=8))

    def _para(self, node, depth):
        start = self._pos()
        self._inline(node, self._ctx())
        self.w.insert("end", "\n")
        self._span(start, self._tag("p", spacing3=6, rmargin=8,
                                    lmargin1=_INDENT * depth, lmargin2=_INDENT * depth))

    def _list(self, node, depth, ordered):
        num = _as_int(node.get("start"), 1)
        for item in node:
            if _tag_of(item) != "li":
                continue
            mark = ("%d. " % num) if ordered else "•  "
            num += 1 if ordered else 0
            start = self._pos()
            self.w.insert("end", mark, self._tag("m", foreground=self._fg("primary", "#000000")))
            self._inline(item, self._ctx())
            self.w.insert("end", "\n")
            pad = _INDENT * depth
            self._span(start, self._tag("l", spacing3=2, lmargin1=pad + 2,
                                        lmargin2=pad + _INDENT, rmargin=8))
            for child in item:
                if _tag_of(child) in ("ul", "ol"):
                    self.block(child, depth + 1)

    def _quote(self, node, depth):
        start = self._pos()
        ctx = self._ctx(fg=self._fg("text_sec", "#666666"), quote=True)
        blocks = [c for c in node if _tag_of(c) in ("p", "ul", "ol", "h1", "h2", "h3")]
        bar = self._tag("qb", foreground=self._fg("border_accent", "#cccccc"))
        if not blocks:
            self.w.insert("end", "▏ ", bar)
            self._inline(node, ctx)
            self.w.insert("end", "\n")
        else:
            for b in blocks:
                self.w.insert("end", "▏ ", bar)
                self._write(_plain(b), ctx) if _tag_of(b) in ("ul", "ol") else self._inline(b, ctx)
                self.w.insert("end", "\n")
        pad = _INDENT * depth + 10
        self._span(start, self._tag("q", spacing1=4, spacing3=6,
                                    lmargin1=pad, lmargin2=pad, rmargin=8))

    def _pre(self, node):
        start = self._pos()
        code = "".join(node.itertext()).replace("\xa0", " ").rstrip("\n")
        self.w.insert("end", code + "\n",
                      self._tag("c", font=(self.mono_font, self.base - 1),
                                foreground=self._fg("text", "#000000"),
                                background=self._fg("elevated", "#f8f8f8")))
        self._span(start, self._tag("cb", spacing1=6, spacing3=8, padx=6, rmargin=8,
                                    lmargin1=_INDENT, lmargin2=_INDENT))

    def _rule(self):
        start = self._pos()
        self.w.insert("end", "─" * 40 + "\n",
                      self._tag("r", foreground=self._fg("border", "#cccccc")))
        self._span(start, self._tag("rb", spacing1=8, spacing3=8))

    def _table(self, node):
        rows = []
        for section in node:
            tag = _tag_of(section)
            if tag == "tr":
                trs = [section]
            elif tag in ("thead", "tbody", "tfoot"):
                trs = list(section)
            else:
                continue
            for tr in trs:
                cells = [_plain(td) for td in tr if _tag_of(td) in ("td", "th")]
                if cells:
                    rows.append(cells)
        if not rows:
            return
        cols = max(len(r) for r in rows)
        rows = [r + [""] * (cols - len(r)) for r in rows]
        f = self._mfont()
        size = self.base - 1
        dash = max(f.measure("─"), 1)
        unit = max(f.measure(" "), 1)
        fg = self._fg("text", "#000000")
        lead = "  "
        # 列宽按像素量：末列不截断（改换行 + lmargin2 对齐），其余列超出才截断
        measured = [max(f.measure(r[i]) for r in rows) for i in range(cols)]
        avail = self.w.winfo_width()
        budget = max((avail if avail > 120 else 700) - 70, 240)
        fixed = [min(w, _TABLE_MAX_PX) for w in measured[:-1]]
        room = max(budget - sum(fixed) - (cols - 1) * 3 * unit, 8 * unit)
        targets = fixed + [min(measured[-1], room)] if cols > 1 else [min(measured[0], budget)]
        # 列间用 \t + tag 的 tabs（像素制表位）定位：分隔符才能真正逐列对齐，
        # 靠空格补齐会有一格左右的漂移；表头底色也能沿制表位铺满整列
        pad = 2 * unit                      # 分隔符 " │ " 两侧各留一格
        stops, col_starts, start_x = [], [], f.measure(lead)
        for i in range(cols):
            col_starts.append(start_x)
            stops.append(start_x + targets[i] + pad)
            start_x = stops[i] + pad
        head_bg = self._fg("elevated", "#f8f8f8")
        sep_tag = self._tag("sp", font=(self.mono_font, size), foreground=self._fg("muted", "#888888"))
        cell_tags = {}

        def cell_tag(i, bg):
            key = (i, bg)
            if key not in cell_tags:
                opts = {"font": (self.mono_font, size), "foreground": fg, "tabs": (stops[i],)}
                if bg:
                    opts["background"] = bg
                cell_tags[key] = self._tag("tc", **opts)
            return cell_tags[key]

        def truncate(text, limit):
            if f.measure(text) > limit:
                while text and f.measure(text + "…") > limit:
                    text = text[:-1]
                text += "…"
            return text

        def row_line(cells, bg=None, rule=False):
            start = self._pos()
            self.w.insert("end", lead, sep_tag)
            for i in range(cols):
                if rule:
                    text = "─" * max(int(targets[i] // dash), 1)
                elif i == cols - 1:
                    text = cells[i]
                else:
                    text = truncate(cells[i], targets[i])
                self.w.insert("end", text, cell_tag(i, bg))
                if i < cols - 1:
                    self.w.insert("end", "─\t─" if rule else " \t ", sep_tag)
            self.w.insert("end", "\n")
            self._span(start, self._tag("tw", spacing1=6, spacing3=8, lmargin1=2,
                                        lmargin2=col_starts[-1] + 2))

        row_line(rows[0], head_bg)
        row_line([""] * cols, rule=True)
        for row in rows[1:]:
            row_line(row)

    def open_link(self, href):
        if href.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(href)


_md_parser = None


def _to_html(text):
    global _md_parser
    if _md_parser is None:
        _md_parser = markdown.Markdown(
            extensions=[TableExtension(), FencedCodeExtension()], output_format="xhtml")
    _md_parser.reset()
    return _md_parser.convert(text)


def render_markdown(widget, md_text, palette, ui_font="Microsoft YaHei UI",
                    mono_font="Consolas", base_size=10):
    """把 Markdown 渲染进 tk.Text，返回是否走了渲染路径（依赖缺失时 False）。

    调用结束时控件保持 normal，是否禁用由调用方决定。
    """
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    for old in getattr(widget, "_md_view_tags", ()):
        widget.tag_delete(old)
    widget._md_view_tags = ()
    if not MD_AVAILABLE:
        widget.insert("end", md_text)
        return False
    if not md_text.strip():
        return True

    renderer = _Renderer(widget, palette or {}, ui_font, mono_font, base_size)
    for child in _lhtml.fromstring("<div>%s</div>" % _to_html(md_text)):
        renderer.block(child)
    widget._md_view_tags = tuple(renderer.tags)

    # 链接点击走控件级绑定：disabled 状态下 tag 绑定未必派发，这样更可靠。
    # 只绑一次，渲染次数再多也不会重复打开。
    holder = getattr(widget, "_md_view_link", None)
    if holder is None:
        holder = {}
        widget._md_view_link = holder

        def _on_click(event):
            try:
                index = widget.index("@%d,%d" % (event.x, event.y))
            except Exception:
                return
            for name in widget.tag_names(index):
                if isinstance(name, str) and name.startswith("link|"):
                    current = holder.get("renderer")
                    if current is not None:
                        current.open_link(name[len("link|"):])
                    return

        widget.bind("<Button-1>", _on_click, add="+")
    holder["renderer"] = renderer
    return True
