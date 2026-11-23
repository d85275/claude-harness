"""Data types shared by the board modules."""
from dataclasses import dataclass, field
from typing import Optional

# Standard ticket states, in flow order. Shared with the config validator.
STATES = ("Planned", "In progress", "In review", "Fixing", "Owner check", "Done", "Blocked", "Deferred")

KNOWN_COLUMNS = ("ID", "Title", "Review", "State", "Commit", "Note")


@dataclass
class Problem:
    kind: str       # e.g. "state-mismatch", "row-no-file"; stable, for tests and filtering
    ticket: str     # ticket ID, or "" when the problem is about a whole file
    message: str

    def line(self):
        return f"{self.ticket}: {self.message}" if self.ticket else self.message

    def as_dict(self):
        return {"kind": self.kind, "ticket": self.ticket, "message": self.message}


@dataclass
class Row:
    """One tracker row."""
    id: str
    link: Optional[str] = None      # link target of the ID cell, if it was a link
    title: str = ""
    review: str = ""
    state: str = ""                 # as written
    commit: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)   # other columns, in header order
    archived: bool = False
    source: str = ""                # tracker path (relative to the root)
    links: list = field(default_factory=list)   # every link target in the row, as written
    line: int = 0                   # 1-based line of the row in its tracker


@dataclass
class TicketFile:
    """One ticket file."""
    id: str
    path: str                       # relative to the root
    title: str = ""
    status: str = ""                # the Status line as written
    archived: bool = False
    labels: list = field(default_factory=list)   # from an optional '## Labels' section
    twins: list = field(default_factory=list)    # IDs named by `Twin: <ID>` lines
    single: bool = False                         # has a `Twin: none` line (no twin wanted)


@dataclass
class Model:
    rows: list = field(default_factory=list)
    files: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # stderr lines for --check; never change the exit code
    tickets: list = field(default_factory=list)    # merged view, see parse.merge
    stats: dict = field(default_factory=dict)      # tracker path -> what its tables gave (parse.new_stats)

    def as_dict(self):
        return {
            "tickets": self.tickets,
            "problems": [p.as_dict() for p in self.problems],
        }
