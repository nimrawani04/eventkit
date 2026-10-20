"""CSV input helpers with UTF-8 BOM handling and physical line numbers."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence, TextIO


@dataclass(frozen=True, slots=True)
class CsvRow:
    """One non-blank CSV record.

    ``line_number`` is the first physical line of the record and
    ``end_line_number`` is the last.  They differ for a quoted field that
    contains a newline.
    """

    line_number: int
    values: tuple[str, ...]
    end_line_number: int

    @property
    def row_number(self) -> int:
        """Alias for the source row's physical starting line."""

        return self.line_number

    @property
    def physical_line_number(self) -> int:
        """Explicit alias for consumers that distinguish physical lines."""

        return self.line_number

    @property
    def line_num(self) -> int:
        """Compatibility alias for the starting physical line."""

        return self.line_number

    @property
    def fields(self) -> tuple[str, ...]:
        return self.values

    def as_dict(self, headers: Sequence[str]) -> dict[str, str]:
        """Map header names to values, filling missing cells with ``""``."""

        return {
            header: self.values[index] if index < len(self.values) else ""
            for index, header in enumerate(headers)
        }


@dataclass(frozen=True, slots=True)
class CsvTable:
    """A header row and the non-blank records that followed it."""

    headers: tuple[str, ...]
    rows: tuple[CsvRow, ...]
    header_line_number: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", tuple(self.headers))
        object.__setattr__(self, "rows", tuple(self.rows))

    def __iter__(self) -> Iterator[CsvRow]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def dict_rows(self, *, include_metadata: bool = False) -> tuple[dict[str, str | int], ...]:
        """Return rows keyed by headers, optionally retaining line metadata.

        Metadata is opt-in so the default remains a faithful representation of
        the CSV columns.  Use ``include_metadata=True`` when mapping rows into
        validation or reporting code that needs source line numbers.
        """

        result: list[dict[str, str | int]] = []
        for row in self.rows:
            values: dict[str, str | int] = dict(row.as_dict(self.headers))
            if include_metadata:
                values["_line"] = row.line_number
                values["_end_line"] = row.end_line_number
            result.append(values)
        return tuple(result)

    def dict_rows_with_metadata(self) -> tuple[dict[str, str | int], ...]:
        """Return header mappings with ``_line`` and ``_end_line`` fields."""

        return self.dict_rows(include_metadata=True)


# Upper-case aliases are convenient for callers that use CSV terminology.
CSVRow = CsvRow
CSVTable = CsvTable


def _without_bom(lines: Iterable[str]) -> Iterator[str]:
    first = True
    for line in lines:
        if first:
            first = False
            if line.startswith("\ufeff"):
                line = line[1:]
        yield line


def _source_context(
    source: str | os.PathLike[str] | TextIO,
    *,
    encoding: str,
) -> tuple[TextIO | None, Iterable[str]]:
    """Open a path or return an existing text stream without closing it."""

    if isinstance(source, (str, os.PathLike)):
        handle = Path(source).open("r", encoding=encoding, newline="")
        return handle, handle
    return None, source


def read_csv(
    source: str | os.PathLike[str] | TextIO,
    *,
    encoding: str = "utf-8-sig",
    has_header: bool = True,
    skip_blank: bool = True,
    strict: bool = False,
) -> CsvTable:
    """Read a UTF-8 CSV while retaining physical record line numbers.

    ``source`` may be a filesystem path or an already-open text stream.  A
    path is opened with ``newline=""`` as required by :mod:`csv`; streams are
    consumed but never closed.  Blank records are skipped by default, while
    quoted newlines are handled by the standard library parser.
    """

    handle, lines = _source_context(source, encoding=encoding)
    try:
        reader = csv.reader(_without_bom(lines), strict=strict)
        headers: tuple[str, ...] = ()
        rows: list[CsvRow] = []
        header_line_number: int | None = None
        previous_end_line = 0

        for values in reader:
            end_line = reader.line_num
            start_line = previous_end_line + 1
            previous_end_line = end_line
            # ``csv.reader`` returns [] for a genuinely empty physical line.
            # Keep rows such as ``,,`` and ``""``: they are registrations with
            # missing fields and must reach EK01 rather than being silently
            # dropped.  A quoted empty field is distinguishable from a blank
            # physical line because the reader returns ``[""]`` for it.
            if skip_blank and not values:
                continue

            record = CsvRow(start_line, tuple(values), end_line)
            if has_header and header_line_number is None:
                headers = record.values
                header_line_number = record.line_number
            else:
                rows.append(record)

        return CsvTable(headers, tuple(rows), header_line_number)
    finally:
        if handle is not None:
            handle.close()


def read_csv_text(
    text: str,
    *,
    has_header: bool = True,
    skip_blank: bool = True,
    strict: bool = False,
) -> CsvTable:
    """Read CSV text, primarily useful for callers and small smoke checks."""

    import io

    return read_csv(
        io.StringIO(text),
        has_header=has_header,
        skip_blank=skip_blank,
        strict=strict,
    )
