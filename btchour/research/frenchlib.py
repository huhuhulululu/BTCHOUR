"""Reader for Kenneth French's data library.

The library ships one zip per dataset. Inside is a single CSV holding several
stacked sections -- monthly returns, then annual, then the number of firms,
then average firm size -- each introduced by a title line and terminated by a
blank line. Nothing is machine-friendly about it, so the parsing lives here
and the statistics live next door in `equityfactors.py`.

Two properties matter for this repo. The file is downloaded whole, so there is
no pagination and therefore no `kalshi-measurement-traps` truncation to hide a
look-ahead in. And the firm-count and firm-size sections let us weight a
portfolio by the market cap it actually holds, which is the difference between
"this premium is real" and "this premium is real in a bucket nobody can put
money into".
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

MISSING = (-99.99, -999.0, -99.99e0)

# The library marks missing data with these sentinels; they are returns in
# percent, so a real -99.99 is not a thing that happens.
_MISSING_TOL = 1e-6


def _is_missing(value: float) -> bool:
    return any(abs(value - sentinel) < _MISSING_TOL for sentinel in MISSING)


@dataclass(frozen=True)
class Section:
    """One stacked block of a French CSV."""

    title: str
    columns: tuple[str, ...]
    rows: dict[str, tuple[float | None, ...]]

    def series(self, column: str) -> dict[str, float]:
        """One column as {period: value}, dropping the missing sentinels."""
        idx = self.columns.index(column)
        out: dict[str, float] = {}
        for period, values in self.rows.items():
            value = values[idx]
            if value is not None:
                out[period] = value
        return out

    def monthly(self) -> "Section":
        """Only the 6-digit YYYYMM rows, so an annual tail cannot leak in."""
        rows = {k: v for k, v in self.rows.items() if len(k) == 6}
        return Section(self.title, self.columns, rows)


def read_zip(path: str) -> str:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{path}: expected exactly one CSV, found {names}")
        with zf.open(names[0]) as fh:
            return io.TextIOWrapper(fh, encoding="latin-1").read()


def parse_sections(text: str) -> list[Section]:
    """Split a French CSV into its stacked sections.

    A section starts at a header row -- a line beginning with a comma, whose
    first field is the empty period label -- and runs until the first line that
    is not a data row. The title is the last non-empty line before the header,
    which is how the library names each block.
    """
    lines = text.splitlines()
    sections: list[Section] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(","):
            i += 1
            continue
        columns = tuple(c.strip() for c in line.split(",")[1:])
        title = ""
        for back in range(i - 1, -1, -1):
            if lines[back].strip():
                title = lines[back].strip()
                break
        rows: dict[str, tuple[float | None, ...]] = {}
        i += 1
        while i < len(lines):
            cells = [c.strip() for c in lines[i].split(",")]
            period = cells[0]
            if not period.isdigit() or len(cells) - 1 != len(columns):
                break
            values: list[float | None] = []
            for cell in cells[1:]:
                try:
                    value = float(cell)
                except ValueError:
                    values.append(None)
                    continue
                values.append(None if _is_missing(value) else value)
            rows[period] = tuple(values)
            i += 1
        if rows:
            sections.append(Section(title, columns, rows))
    return sections


def section_by_title(sections: list[Section], needle: str) -> Section:
    """The first section whose title contains `needle`, case-insensitively."""
    needle = needle.lower()
    for section in sections:
        if needle in section.title.lower():
            return section
    titles = [s.title for s in sections]
    raise KeyError(f"no section matching {needle!r}; have {titles}")


def load(path: str) -> list[Section]:
    return parse_sections(read_zip(path))
