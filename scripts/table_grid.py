"""
Generic HTML-table-model grid builder: expands ROWSPAN/COLSPAN into a dense
2D grid, the same algorithm browsers use. Shared by every parser (exchange's
real HTML tables and DSD-XML's TABLE/TBODY/THEAD/TR/TD-TE-TU-TH tables use the
same tag names for these structural elements and the same ROWSPAN/COLSPAN
attributes).
"""
from common_parse import collapse_ws

CELL_TAGS = ("TD", "TE", "TU", "TH")


def _int_attr(node, name, default=1):
    v = node.get(name)
    if v is None:
        return default
    try:
        n = int(str(v).strip())
        return n if n > 0 else default
    except ValueError:
        return default


class Cell:
    """One logical cell (its ORIGIN occupies col_start..col_start+colspan-1 in
    row_start..row_start+rowspan-1; other grid slots merely reference it)."""

    __slots__ = ("node", "row_start", "col_start", "rowspan", "colspan", "text")

    def __init__(self, node, row_start, col_start, rowspan, colspan):
        self.node = node
        self.row_start = row_start
        self.col_start = col_start
        self.rowspan = rowspan
        self.colspan = colspan
        self.text = collapse_ws(node.text_content())

    @property
    def tag(self):
        return self.node.tag


class GridRow:
    """One rendered row of a table: which cells make their FIRST appearance
    here (own_cells, in column order - these are the ones this row's own
    markup actually declared), and the full set of columns occupied in this
    row (including cells continuing from a rowspan above), for column-count
    bookkeeping."""

    __slots__ = ("index", "own_cells", "all_cols", "is_header_tag_row")

    def __init__(self, index):
        self.index = index
        self.own_cells = []  # list[Cell], in column order, first-appearance-in-this-row only
        self.all_cols = {}  # col_index -> Cell (includes inherited spans)
        self.is_header_tag_row = False  # True if this <TR> lives under a <THEAD>


def find_rows(table_node):
    """Return list of (tr_node, is_header) in document order, looking inside
    THEAD/TBODY if present, else direct TR children."""
    rows = []
    thead = table_node.find_first("THEAD")
    if thead is not None:
        for tr in thead.find_all("TR"):
            rows.append((tr, True))
    for container_tag in ("TBODY",):
        tbody = table_node.find_first(container_tag)
        if tbody is not None:
            for tr in tbody.find_all("TR"):
                rows.append((tr, False))
    if thead is None and table_node.find_first("TBODY") is None:
        # bare TABLE/TR with no THEAD/TBODY wrapper
        for tr in table_node.find_all("TR"):
            rows.append((tr, False))
    return rows


def build_grid(table_node, cell_tags=CELL_TAGS):
    """Standard browser table-grid algorithm. Returns list[GridRow]."""
    rows_src = find_rows(table_node)
    pending = {}  # col_index -> [remaining_rows, Cell]
    grid_rows = []

    for r_idx, (tr, is_header) in enumerate(rows_src):
        grow = GridRow(r_idx)
        grow.is_header_tag_row = is_header

        # 1) columns still covered by an active rowspan from above
        for col, (remaining, cell) in list(pending.items()):
            grow.all_cols[col] = cell
        # (own_cells stays empty for inherited coverage - filled below with
        #  only genuinely-new cells from this row's own markup)

        occupied = set(grow.all_cols.keys())
        col_cursor = 0

        def next_free_col(start):
            c = start
            while c in occupied:
                c += 1
            return c

        for child in tr.children:
            if child.tag not in cell_tags:
                continue
            colspan = _int_attr(child, "COLSPAN", 1)
            rowspan = _int_attr(child, "ROWSPAN", 1)
            col_cursor = next_free_col(col_cursor)
            cell = Cell(child, r_idx, col_cursor, rowspan, colspan)
            grow.own_cells.append(cell)
            for c in range(col_cursor, col_cursor + colspan):
                grow.all_cols[c] = cell
                occupied.add(c)
            if rowspan > 1:
                for c in range(col_cursor, col_cursor + colspan):
                    pending[c] = [rowspan - 1, cell]
            col_cursor += colspan

        # age out / remove pending entries that just supplied this row
        for col in list(pending.keys()):
            remaining, cell = pending[col]
            if cell.row_start == r_idx:
                continue  # just created above, don't decrement yet
            remaining -= 1
            if remaining <= 0:
                del pending[col]
            else:
                pending[col][0] = remaining

        grid_rows.append(grow)

    return grid_rows


def table_col_count(grid_rows):
    n = 0
    for gr in grid_rows:
        if gr.all_cols:
            n = max(n, max(gr.all_cols.keys()) + 1)
    return n
