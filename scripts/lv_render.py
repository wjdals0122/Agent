"""
Shared label/value table rendering, used by every parser (exchange, major,
holding, periodic). Two rendering modes per table:

- "data" table: a genuine header+rows comparison table -> rendered as a real
  markdown table.
- "form" table: each row is 1+ label cell(s) followed by 1+ value cell(s)
  (DART's disclosure-form layout) -> rendered as nested label/value bullets.

The row_ctx (category-promotion) and rowspan-inheritance rules below encode
exceptions 4-7 from the project's parsing-exceptions spec: drop meaningless
'-' values (keep meaningful negatives like "해당사항없음"), merge duplicate
*label*-role cells only (never value cells, or "- | -" comparison rows lose a
column), promote leading labels in an "L L V" row to a sticky category for
following rows, and derive that stickiness from actual rowspan coverage
rather than a blanket "rowspan means hierarchy" assumption (a row that
supplies its own complete label+value pair is never forced to inherit).
"""
import re

from common_parse import collapse_ws
from table_grid import build_grid, table_col_count

# a spaced-out colon label on a cover page, e.g. "회 사 명 :" - matched
# against the FIRST of exactly 2 own cells in a row; ported from the
# original reference parser (parser/holding_parser.py's _colon_kv), which
# found this pattern responsible for 100% of a 6,349-cell discrepancy in its
# own cell-level validation pass.
_COLON_LABEL_RE = re.compile(r"^(.{1,30}?)\s*[:：]\s*$")

MEANINGFUL_DASH_VALUES = {"해당사항없음", "미해당", "해당", "아니오", "예"}

KEEP_EMPTY = False  # set True to emulate --keep-empty (preserve '-' rows verbatim)


def set_keep_empty(value):
    global KEEP_EMPTY
    KEEP_EMPTY = value


def _is_droppable_dash(value_text):
    if KEEP_EMPTY:
        return False
    return value_text.strip() == "-"


def render_data_table(grid_rows, ncols):
    """Header (first row(s) marked is_header_tag_row, or - for exchange -
    the caller pre-marks header rows via GridRow.is_header_tag_row) + data
    rows -> a markdown pipe table."""
    header_rows = [r for r in grid_rows if r.is_header_tag_row]
    data_rows = [r for r in grid_rows if not r.is_header_tag_row]
    if not header_rows:
        header_rows, data_rows = [grid_rows[0]], grid_rows[1:]

    def row_cells_text(gr):
        cells = ["" for _ in range(ncols)]
        for c, cell in gr.all_cols.items():
            if c < ncols:
                cells[c] = cell.text.replace("|", "\\|").replace("\n", " ")
        return cells

    lines = []
    if header_rows:
        # A multi-row header (e.g. a parent header spanning 2 cols via
        # COLSPAN on row 1, sub-headers on row 2) must have EVERY header
        # row's text represented per column, not just the last row - a
        # colspan-only header cell (no ROWSPAN) only ever appears in its own
        # row's all_cols, so using only header_rows[-1] silently drops real
        # header content (e.g. "감사계약내역" grouping "보수"/"시간").
        per_col_parts = [[] for _ in range(ncols)]
        for hr in header_rows:
            texts = row_cells_text(hr)
            for c in range(ncols):
                t = texts[c]
                if t and (not per_col_parts[c] or per_col_parts[c][-1] != t):
                    per_col_parts[c].append(t)
        header_cells = [" > ".join(parts) for parts in per_col_parts]
    else:
        header_cells = ["" for _ in range(ncols)]
    lines.append("| " + " | ".join(header_cells) + " |")
    lines.append("|" + "|".join(["---"] * ncols) + "|")
    for gr in data_rows:
        lines.append("| " + " | ".join(row_cells_text(gr)) + " |")
    return "\n".join(lines)


def _dedupe_adjacent_labels(tokens):
    """Rule 5: merge ADJACENT cells that are both label-role and have
    identical text (rowspan/colspan visual duplication artifacts). Value-role
    cells are never touched here, even if their text happens to coincide
    (e.g. a correction-comparison row "- | -")."""
    out = []
    for role, text, cell in tokens:
        if (
            out
            and role == "L"
            and out[-1][0] == "L"
            and out[-1][1] == text
        ):
            continue
        out.append((role, text, cell))
    return out


class FormTableRenderer:
    """Stateful across the rows of ONE table: tracks the sticky row_ctx
    (category promoted from an "L L V" row, or inherited from an active
    rowspan on the label column) and the "already displayed" rowspan cells
    (a rowspan-covered slot must not re-emit its label text as a duplicate
    bullet on every row it spans)."""

    def __init__(self):
        self.row_ctx = []  # list[str] - promoted category labels, sticky until redefined
        self.items = []  # list[(display_label, value)]
        self._pending_prefix = ""

    def feed_row(self, grid_row, is_value_fn):
        own = grid_row.own_cells
        if not own:
            return  # fully rowspan-covered row with no own cells - nothing new to say

        if len(own) == 2:
            m = _COLON_LABEL_RE.match(own[0].text)
            if m:
                self.row_ctx = []
                self._emit(re.sub(r"\s+", "", m.group(1)), own[1].text)
                return

        # a rowspan cell from an EARLIER row may still cover a column to the
        # left of this row's own cells (e.g. "2. 투자내역"[rowspan=4] with
        # this row supplying only its own "자기자본(원) | value" pair to the
        # right of it) - that covering label is real inherited grouping
        # context even though this row also has its own complete label, so
        # surface it as a prefix rather than let self.row_ctx (which a
        # single-own-label row resets) swallow it.
        own_cols = {c.col_start for c in own}
        first_new_col = min(own_cols)
        inherited = []
        for col in sorted(grid_row.all_cols):
            if col >= first_new_col:
                break
            if col in own_cols:
                continue
            covering = grid_row.all_cols[col]
            if covering.row_start < grid_row.index and not is_value_fn(covering):
                if not inherited or inherited[-1] != covering.text:
                    inherited.append(covering.text)
        self._pending_prefix = " > ".join(inherited)

        tokens = [("V" if is_value_fn(c) else "L", c.text, c) for c in own]
        tokens = _dedupe_adjacent_labels(tokens)

        # reversed order: exactly one V then one L ("U L" - value-before-label)
        if len(tokens) == 2 and tokens[0][0] == "V" and tokens[1][0] == "L":
            tokens = [tokens[1], tokens[0]]

        i = 0
        n = len(tokens)
        items_at_row_start = len(self.items)
        while i < n:
            run_start = i
            while i < n and tokens[i][0] == "L":
                i += 1
            labels_run = tokens[run_start:i]

            if i >= n:
                # trailing label(s) with nothing after
                if len(labels_run) >= 2:
                    *ctx, last = labels_run
                    self.row_ctx = [t[1] for t in ctx]
                    self._emit(" > ".join(self.row_ctx + [last[1]]), "")
                elif len(labels_run) == 1:
                    # single trailing label right after a value emitted
                    # earlier in THIS row ("L V L") -> unit/qualifier suffix
                    # on that value, not a new orphan item. Guard on
                    # self.items actually having grown THIS row, not merely
                    # "something was attempted" - a leading dash value can be
                    # silently dropped by _emit (rule 4), which must not
                    # leave a stale earlier row's item to be corrupted here.
                    if len(self.items) > items_at_row_start and i - 1 == n - 1 and n >= 3:
                        suffix = labels_run[0][1]
                        last_label, last_value = self.items[-1]
                        self.items[-1] = (last_label, last_value + suffix)
                    else:
                        self.row_ctx = []
                        self._emit(labels_run[0][1], "")
                break

            value_tok = tokens[i]
            i += 1

            if len(labels_run) >= 2:
                *ctx, last = labels_run
                self.row_ctx = [t[1] for t in ctx]
                label_display = " > ".join(self.row_ctx + [last[1]])
            elif len(labels_run) == 1:
                self.row_ctx = []
                label_display = labels_run[0][1]
            else:
                label_display = " > ".join(self.row_ctx) if self.row_ctx else ""

            self._emit(label_display, value_tok[1])

    def _emit(self, label, value):
        if self._pending_prefix:
            label = (self._pending_prefix + " > " + label) if label else self._pending_prefix
        label = " > ".join(p for p in label.split(" > ") if p.strip())
        value = value.strip()
        if _is_droppable_dash(value) and value not in MEANINGFUL_DASH_VALUES:
            return
        self.items.append((label, value))

    def to_markdown(self):
        lines = []
        for label, value in self.items:
            label = label.strip()
            if not label and not value:
                continue
            if value:
                lines.append(f"- **{label}**: {value}" if label else f"- {value}")
            else:
                lines.append(f"- **{label}**")
        return "\n".join(lines)


def _is_shaded_header_cell(cell):
    mark = cell.node.get("USERMARK", "")
    return cell.tag == "TD" and mark.upper().startswith("BC0")


def _apply_shaded_header(grid_rows):
    """A table with no real <THEAD> can still mark its header row purely via
    background-shaded (USERMARK="BC0...") TD cells - common in financial-
    statement-note tables. If the first row's own cells are ALL shaded like
    this, treat it as the header row so it renders as a data table instead of
    being mistaken for label/value form cells."""
    if len(grid_rows) < 2 or not grid_rows[0].own_cells:
        return False  # a table that is ENTIRELY one shaded row has no data
        # rows to be a header for - leave it as a plain form/heading, not a
        # misjudged data table
    if all(_is_shaded_header_cell(c) for c in grid_rows[0].own_cells):
        grid_rows[0].is_header_tag_row = True
        return True
    return False


def is_data_table(table_node, grid_rows):
    """A table is a 'data' comparison table (real header + data rows) rather
    than a label/value form when it has a genuine THEAD, or a shaded-header
    first row (see _apply_shaded_header)."""
    if table_node.find_first("THEAD") is not None:
        return True
    return _apply_shaded_header(grid_rows)


def render_mixed_table(table_node, is_value_fn):
    """Row-by-row classification for tables that MIX row kinds in one table
    (exchange/KRX disclosure forms): a full-width single cell is either a
    sub-heading (label-role) or free-text line (value-role); a multi-cell
    all-label row followed by rows of matching column count that DO carry
    value cells is a header+data comparison block; anything else is an
    ordinary label/value row fed through the shared FormTableRenderer so
    row_ctx / dash-dropping / dedup rules stay consistent with the DSD
    parsers. row_ctx state persists across the whole table."""
    grid_rows = build_grid(table_node)
    if not grid_rows:
        return ""
    ncols = table_col_count(grid_rows)

    out_blocks = []
    renderer = FormTableRenderer()

    def flush_form():
        md = renderer.to_markdown()
        if md:
            out_blocks.append(md)
        renderer.items.clear()

    i = 0
    n = len(grid_rows)
    while i < n:
        gr = grid_rows[i]
        own = gr.own_cells

        if len(own) == 1 and own[0].colspan >= ncols and ncols > 1:
            flush_form()
            text = own[0].text.strip()
            if text:
                if is_value_fn(own[0]):
                    out_blocks.append(text)
                else:
                    out_blocks.append(f"**{text}**")
            i += 1
            continue

        if len(own) >= 2 and all(not is_value_fn(c) for c in own):
            # header candidate: does the table actually continue with data
            # rows of the same shape carrying value cells?
            j = i + 1
            data_rows = []
            while j < n:
                nxt = grid_rows[j].own_cells
                if len(nxt) == 0:
                    data_rows.append(grid_rows[j])
                    j += 1
                    continue
                if len(nxt) == len(own) or (len(nxt) >= 1 and any(is_value_fn(c) for c in nxt)):
                    if len(nxt) == 1 and nxt[0].colspan >= ncols:
                        break
                    data_rows.append(grid_rows[j])
                    j += 1
                    continue
                break
            if data_rows:
                flush_form()
                header_texts = [c.text.replace("|", "\\|") for c in own]
                lines = ["| " + " | ".join(header_texts) + " |", "|" + "|".join(["---"] * len(own)) + "|"]
                for dr in data_rows:
                    cells = ["" for _ in range(ncols)]
                    for c, cell in dr.all_cols.items():
                        if c < ncols:
                            cells[c] = cell.text.replace("|", "\\|").replace("\n", " ")
                    lines.append("| " + " | ".join(cells) + " |")
                out_blocks.append("\n".join(lines))
                i = j
                continue

        renderer.feed_row(gr, is_value_fn)
        i += 1

    flush_form()
    return "\n\n".join(b for b in out_blocks if b.strip())


def render_table(table_node, is_value_fn, force_mode=None):
    """Returns markdown text for one TABLE node. `is_value_fn(cell) -> bool`
    decides label vs value for form-mode rows (ignored in data mode)."""
    grid_rows = build_grid(table_node)
    if not grid_rows:
        return ""
    ncols = table_col_count(grid_rows)

    mode = force_mode or ("data" if is_data_table(table_node, grid_rows) else "form")
    if mode == "data":
        return render_data_table(grid_rows, ncols)

    renderer = FormTableRenderer()
    for gr in grid_rows:
        renderer.feed_row(gr, is_value_fn)
    return renderer.to_markdown()
