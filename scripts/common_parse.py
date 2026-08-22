"""
Shared parsing core for all DART document-group parsers (exchange/major/holding/periodic).

Design rationale (see corpus profiling in results/01_profile/ for the underlying
facts this is built on):

- All raw files are UTF-8 bytes regardless of what any declared charset/encoding
  says (exchange HTML falsely claims euc-kr; DSD-XML correctly claims utf-8 but
  that claim is irrelevant here since we never trust declarations - we just
  decode the bytes as UTF-8 directly).
- DSD-XML (major/holding/periodic) is not well-formed XML: it contains bare
  '<word>' bracket annotations and unescaped '&' in running text. A real XML
  parser (lxml strict) chokes or silently drops content on ~72% of files (see
  STEP 1 profiling). Python's stdlib html.parser is already lenient enough to
  treat '<' followed by a non-tag-like token as literal text, so it is used
  as the tokenizer for BOTH the DSD-XML dialect and the real embedded-HTML
  dialect (exchange docs) - one shared tree builder, two front-ends.
- The only genuinely tricky bit stdlib html.parser gets wrong for our purposes
  is '&word' handling: it will call handle_entityref('word') for ANY '&' + a
  letter sequence, whether or not a ';' followed in the source, and whether or
  not 'word' is a real HTML entity name. If we always add back a ';' we
  fabricate characters that were never in the source (e.g. "R&D" -> "R&D;").
  So entity resolution here is name-set-gated, not "assume a real entity".
"""
import re
from html.parser import HTMLParser
from html.entities import html5

# ---------------------------------------------------------------------------
# entity handling
# ---------------------------------------------------------------------------
_REAL_ENTITY_CHAR = {}
for _k, _v in html5.items():
    _name = _k[:-1] if _k.endswith(";") else _k
    # keep the first (semicolon-terminated forms take priority if both exist)
    if _name not in _REAL_ENTITY_CHAR or _k.endswith(";"):
        _REAL_ENTITY_CHAR[_name] = _v


def resolve_entity_name(name):
    """Return the decoded character for a KNOWN html5 entity name, else None
    (meaning: not a real entity - the caller must preserve '&name' literally,
    never inventing a ';' that wasn't in the source)."""
    return _REAL_ENTITY_CHAR.get(name)


# tags whose content must never be interpreted as markup (raw CSS/JS text)
_RAWTEXT_TAGS = {"script", "style"}

# tags with no closing tag in this corpus - auto-close immediately on open so
# stray/missing end tags never desync the element stack
VOID_TAGS = {
    "BR", "COL", "IMG", "PGBRK", "HR", "INPUT", "META", "LINK",
    "AREA", "BASE", "EMBED", "SOURCE", "TRACK", "WBR",
}


class Node:
    """Either an element node (tag is a str, uppercased) or a text node
    (tag is None, text holds the string)."""
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag, attrs=None, text=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = None
        self.text = text

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def get(self, key, default=None):
        return self.attrs.get(key, default)

    def is_element(self):
        return self.tag is not None

    def iter_elements(self):
        """Depth-first, document-order iteration over element (non-text) nodes,
        starting with self."""
        if self.is_element():
            yield self
        for c in self.children:
            if c.is_element():
                yield from c.iter_elements()

    def find_all(self, tag):
        return [n for n in self.iter_elements() if n.tag == tag and n is not self]

    def find_first(self, tag):
        for n in self.iter_elements():
            if n.tag == tag and n is not self:
                return n
        return None

    def text_content(self):
        """Concatenate all descendant text, in document order. Text is joined
        with a single space at every CHILD-element boundary that doesn't
        already have whitespace there (never inside one text node's own raw
        content, since that would insert spaces the source never had). This
        is what keeps a bold inline sub-heading like '<SPAN>나. 소수주주권
        </SPAN>회사는...' (no space in source) from silently gluing into
        '나. 소수주주권회사는...' - the two pieces come from different
        children, so they get a boundary space; but text an entity-fallback
        merges into the SAME text node (e.g. 'P' + literal '&A' from an
        unescaped "P&A인수") is never re-split, so genuine no-space source
        text is never disturbed."""
        parts = []
        for c in self.children:
            if c.tag is None:
                parts.append(c.text or "")
            else:
                parts.append(c.text_content())
        out = []
        for p in parts:
            if not p:
                continue
            if out and not out[-1][-1].isspace() and not p[0].isspace():
                out.append(" ")
            out.append(p)
        return "".join(out)


class LenientTreeBuilder(HTMLParser):
    """Builds a Node tree from raw markup that may be real HTML or malformed
    DSD-XML. Tag/attribute names are uppercased for structural matching;
    text content is preserved exactly as decoded (case, whitespace, etc)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = Node("#ROOT#")
        self._stack = [self.root]
        self._rawtext_tag = None  # currently-open script/style (lowercase)

    def _top(self):
        return self._stack[-1]

    def _append_text(self, s):
        if s == "":
            return
        top = self._top()
        if top.children and top.children[-1].tag is None:
            top.children[-1].text += s
        else:
            top.append(Node(None, text=s))

    def handle_starttag(self, tag, attrs):
        upper = tag.upper()
        attrd = {k.upper(): v for k, v in attrs}
        node = Node(upper, attrd)
        self._top().append(node)
        if tag in _RAWTEXT_TAGS:
            self._rawtext_tag = tag
            self._stack.append(node)
        elif upper in VOID_TAGS:
            pass  # no children expected; do not push
        else:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # self-closed like <COL/>
        upper = tag.upper()
        attrd = {k.upper(): v for k, v in attrs}
        self._top().append(Node(upper, attrd))

    def handle_endtag(self, tag):
        if self._rawtext_tag is not None:
            if tag == self._rawtext_tag:
                self._rawtext_tag = None
                if len(self._stack) > 1:
                    self._stack.pop()
            return
        upper = tag.upper()
        # search up the stack (excluding root) for a matching open element
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == upper:
                del self._stack[i:]
                return
        # no matching open tag anywhere - stray end tag, ignore (do not crash,
        # do not corrupt the stack)

    def handle_data(self, data):
        if self._rawtext_tag is not None:
            self._append_text(data)
            return
        self._append_text(data)

    def handle_entityref(self, name):
        ch = resolve_entity_name(name)
        self._append_text(ch if ch is not None else "&" + name)

    def handle_charref(self, name):
        try:
            if name.lower().startswith("x"):
                cp = int(name[1:], 16)
            else:
                cp = int(name)
            self._append_text(chr(cp))
        except (ValueError, OverflowError):
            self._append_text("&#" + name + ";")

    def _abs_offset(self):
        line, col = self.getpos()
        if line == 1:
            return col
        # cache newline positions once per parse (this parser instance feeds
        # its whole input in a single feed() call) instead of re-scanning the
        # full buffer on every bogus-comment occurrence - matters on the
        # largest source files (tens of MB) with many bracket annotations.
        cache = getattr(self, "_line_starts", None)
        if cache is None or cache[0] is not self.rawdata:
            starts = [0]
            idx = self.rawdata.find("\n")
            while idx != -1:
                starts.append(idx + 1)
                idx = self.rawdata.find("\n", idx + 1)
            cache = (self.rawdata, starts)
            self._line_starts = cache
        return cache[1][line - 1] + col

    def handle_comment(self, data):
        # Python's html.parser routes BOTH real "<!-- -->" comments and
        # "bogus comments" (anything starting "<!", "<?", or "</tag-that-
        # doesn't-look-like-a-tag-name" that it can't otherwise parse) through
        # this single callback with the same shape, so a literal '<정정 전>'-
        # style bracket annotation whose closing half looks like '</단어>'
        # arrives here too. Dropping unconditionally would silently delete
        # real source text (violates "don't fabricate/lose original text").
        # Only genuine "<!--...-->" comments are safe to drop; everything
        # else is reconstructed byte-for-byte from rawdata and kept as text.
        start = self._abs_offset()
        if self.rawdata[start : start + 4] == "<!--":
            return  # genuine comment (or XML processing-instruction-style
            # remnant inside one, e.g. "<!--?disable-output-escaping?-->") -
            # not visible content, safe to drop.
        end = self.rawdata.find(">", start)
        if end == -1:
            self._append_text(self.rawdata[start:])
        else:
            self._append_text(self.rawdata[start : end + 1])

    def handle_decl(self, decl):
        pass

    def unknown_decl(self, data):
        pass

    def handle_pi(self, data):
        pass


def parse_markup(raw_bytes):
    """Decode raw bytes as UTF-8 (ignoring any declared charset) and build a
    lenient Node tree. Returns the synthetic #ROOT# node (may have >1 child
    if the source has no single root element, e.g. bare HTML fragments)."""
    text = raw_bytes.decode("utf-8", errors="replace")
    builder = LenientTreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


WS_RE = re.compile(r"[ \t\r\n　]+")


def collapse_ws(s):
    return WS_RE.sub(" ", s).strip()
