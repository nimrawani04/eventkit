"""Deterministic, escaped agenda HTML for a parsed schedule."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Iterable

from .schedule import ScheduleResult, Session


def _sessions(value: ScheduleResult | Iterable[Session]) -> tuple[Session, ...]:
    if isinstance(value, ScheduleResult):
        return tuple(value.sessions)
    return tuple(value)


def _sort_key(session: Session) -> tuple[object, ...]:
    return (session.start.date(), session.start, session.end, session.title, session.row_number)


def _clash_ids(session: Session) -> tuple[str, ...]:
    return tuple(code for code in session.issue_ids if code in {"SC02", "SC03"})


def render_agenda(
    schedule: ScheduleResult | Iterable[Session],
    event_name: str = "Event agenda",
) -> bytes:
    """Render sessions grouped by their parsed local calendar day."""

    sessions = sorted(_sessions(schedule), key=_sort_key)
    grouped: dict[object, list[Session]] = {}
    for session in sessions:
        grouped.setdefault(session.start.date(), []).append(session)

    day_sections: list[str] = []
    for day, day_sessions in grouped.items():
        cards: list[str] = []
        for session in day_sessions:
            clashes = _clash_ids(session)
            class_name = "session clash" if clashes else "session"
            issue_text = ",".join(clashes)
            marker = (
                f'          <span class="clash-marker">Clash: {escape(issue_text)}</span>\n'
                if clashes
                else ""
            )
            speaker = (
                f'          <p class="speaker">Speaker: {escape(session.speaker, quote=True)}</p>\n'
                if session.speaker
                else ""
            )
            cards.append(
                f'        <article class="{class_name}" data-row="{session.row_number}"'
                f' data-issues="{escape(issue_text, quote=True)}">\n'
                f"          <h3>{escape(session.title, quote=True)}</h3>\n"
                f'          <p class="time">{session.start:%H:%M}–{session.end:%H:%M}</p>\n'
                f'          <p class="room">Room: {escape(session.room, quote=True)}</p>\n'
                f"{speaker}"
                f"{marker}"
                "        </article>"
            )
        day_sections.append(
            f'    <section class="day" data-date="{day.isoformat()}">\n'
            f"      <h2>{day.isoformat()}</h2>\n"
            f"{chr(10).join(cards)}\n"
            "    </section>"
        )

    body = (
        "\n".join(day_sections)
        if day_sections
        else '    <p class="empty">No scheduled sessions.</p>'
    )
    document = f'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(event_name, quote=True)} — Agenda</title>
    <style>
      :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
      body {{ margin: 2rem auto; max-width: 60rem; color: #17202a; background: #fff; }}
      h1 {{ margin-bottom: 1.5rem; color: #1e3a5f; }}
      .day {{ margin: 2rem 0; }}
      .day h2 {{ border-bottom: 2px solid #b7c2cc; color: #1e3a5f; padding-bottom: .35rem; }}
      .session {{ border: 1px solid #b7c2cc; margin: 1rem 0; padding: .8rem 1rem; }}
      .session h3 {{ margin: 0 0 .35rem; }}
      .time, .room, .speaker {{ margin: .25rem 0; }}
      .time {{ font-weight: 700; }}
      .clash {{ border-color: #b42318; background: #fff3f2; }}
      .clash-marker {{ color: #b42318; font-weight: 700; }}
      .empty {{ color: #536471; }}
      @media print {{ body {{ margin: 0; max-width: none; }} .session {{ break-inside: avoid; }} }}
    </style>
  </head>
  <body>
    <h1>{escape(event_name, quote=True)}</h1>
{body}
  </body>
</html>
'''
    return document.encode("utf-8")


class _AgendaParser(HTMLParser):
    """Small structural parser used by the agenda post-check."""

    def __init__(self) -> None:
        super().__init__()
        self.day_count = 0
        self.session_count = 0
        self.clash_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section" and "day" in (attributes.get("class") or "").split():
            self.day_count += 1
        if tag == "article" and "session" in (attributes.get("class") or "").split():
            self.session_count += 1
            if "clash" in (attributes.get("class") or "").split():
                self.clash_count += 1


def verify_agenda_html(
    content: bytes | str,
    expected_sessions: int | Iterable[Session] | ScheduleResult,
) -> None:
    """Raise ``ValueError`` unless agenda HTML has the expected session count."""

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"agenda is not valid UTF-8: {exc}") from exc
    else:
        text = content
    parser = _AgendaParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"agenda is not valid HTML: {exc}") from exc
    if isinstance(expected_sessions, int):
        expected_count = expected_sessions
        expected_days: set[object] | None = None
    elif isinstance(expected_sessions, ScheduleResult):
        expected_count = len(expected_sessions.sessions)
        expected_days = {session.start.date() for session in expected_sessions.sessions}
    else:
        expected_tuple = tuple(expected_sessions)
        expected_count = len(expected_tuple)
        expected_days = {session.start.date() for session in expected_tuple}
    if parser.session_count != expected_count:
        raise ValueError("agenda session count does not match the schedule")
    if expected_days is not None and parser.day_count != len(expected_days):
        raise ValueError("agenda day grouping does not match the schedule")


render_agenda_html = render_agenda


__all__ = ["render_agenda", "render_agenda_html", "verify_agenda_html"]
