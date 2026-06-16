"""Scan Claude Code JSONL transcripts and aggregate token usage per directory.

Claude Code stores one folder per working directory under
``~/.claude/projects/<encoded-cwd>/``, with one ``*.jsonl`` transcript per
session. Each assistant message carries a ``message.usage`` block. This module
reads those files and rolls them up — the reliable per-directory method, since
the logs are already partitioned by directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects"))
)

# Rough blended public $/MTok rates. Override via env if your mix differs.
# Cache reads are ~10% of input, which is why raw token totals overstate cost.
RATE_INPUT = float(os.environ.get("CCTOP_RATE_INPUT", "3.00"))
RATE_OUTPUT = float(os.environ.get("CCTOP_RATE_OUTPUT", "15.00"))
RATE_CACHE_WRITE = float(os.environ.get("CCTOP_RATE_CACHE_WRITE", "3.75"))
RATE_CACHE_READ = float(os.environ.get("CCTOP_RATE_CACHE_READ", "0.30"))

HOME = str(Path.home())

# ---------------------------------------------------------------------------
# Per-family rate table (D3).  Each family's $/MTok rates are independently
# env-overridable via CCTOP_RATE_<FAMILY>_<CLASS>.
# ---------------------------------------------------------------------------

# Display / column order for the four named model families.
FAMILIES = ("haiku", "sonnet", "opus", "fable")


@dataclass(frozen=True)
class ModelRate:
    input: float
    output: float
    cache_write: float
    cache_read: float


def _rate(family: str, defaults: ModelRate) -> ModelRate:
    """Per-family rate, each class overridable via CCTOP_RATE_<FAMILY>_<CLASS>."""
    def g(cls: str, d: float) -> float:
        return float(os.environ.get(f"CCTOP_RATE_{family.upper()}_{cls}", d))

    return ModelRate(
        input=g("INPUT", defaults.input),
        output=g("OUTPUT", defaults.output),
        cache_write=g("CACHE_WRITE", defaults.cache_write),
        cache_read=g("CACHE_READ", defaults.cache_read),
    )


# "default" family == today's blended rates (keeps RATE_* + existing Usage.cost intact).
# "fable" has its own real rates ($10/$50), distinct from Opus ($5/$25) — no longer a placeholder.
RATES: dict[str, ModelRate] = {
    "opus":      _rate("OPUS",    ModelRate(5.0,  25.0,  6.25,  0.50)),
    "sonnet":    _rate("SONNET",  ModelRate(3.0,  15.0,  3.75,  0.30)),
    "haiku":     _rate("HAIKU",   ModelRate(1.0,  5.0,   1.25,  0.10)),
    "fable":     _rate("FABLE",   ModelRate(10.0, 50.0,  12.50, 1.00)),
    "synthetic": ModelRate(0.0, 0.0, 0.0, 0.0),
    "default":   ModelRate(RATE_INPUT, RATE_OUTPUT, RATE_CACHE_WRITE, RATE_CACHE_READ),
}

# Client markup multiplier (D4). Unset → CLIENT $ equals EST $.
MARGIN = float(os.environ.get("CCTOP_MARGIN", "1.0"))


def family_of(model: str | None) -> str:
    """Resolve a model id to its family key (D7).

    Case-insensitive substring match in priority order: opus → sonnet → haiku → fable.
    "<synthetic>" → "synthetic". Anything else (incl. None) → "default".
    """
    if model is None:
        return "default"
    if model == "<synthetic>":
        return "synthetic"
    lower = model.lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in lower:
            return fam
    return "default"


def cost_of(usage: "Usage", family: str) -> float:
    """Compute the dollar cost of *usage* using *family*'s $/MTok rates."""
    r = RATES[family]
    return (
        usage.input * r.input
        + usage.output * r.output
        + usage.cache_create * r.cache_write
        + usage.cache_read * r.cache_read
    ) / 1e6


def client_cost(cost: float) -> float:
    """Apply the markup multiplier (CCTOP_MARGIN) to a cost figure."""
    return cost * MARGIN


def cwd_in_scope(session_cwd: str, scope: str | None) -> bool:
    """True if *session_cwd* belongs to *scope* (the launch directory).

    scope is None → global (everything matches). Otherwise a session matches when
    its cwd is the scope directory itself OR any directory nested beneath it — so
    launching in ``~/Projects/wbm-booking`` folds in its ``.claude/worktrees/*``
    and ``.claude/agents`` sessions rather than dropping them.
    """
    if scope is None:
        return True
    scope = scope.rstrip(os.sep)
    return session_cwd == scope or session_cwd.startswith(scope + os.sep)


def _local_day(timestamp: str | None) -> str:
    """Convert a UTC ISO timestamp to a local YYYY-MM-DD string.

    Handles the trailing-Z form (rejected by datetime.fromisoformat before
    Python 3.11) by normalising it to +00:00 before parsing.  Returns
    "unknown" on any failure — never raises.
    """
    if not timestamp:
        return "unknown"
    try:
        ts = timestamp
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        # Attach UTC if no tzinfo, then convert to local wall-clock time.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Core token-usage dataclass
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_create: int = 0
    cache_read: int = 0
    sessions: int = 0

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_create += other.cache_create
        self.cache_read += other.cache_read
        self.sessions += other.sessions

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_create + self.cache_read

    @property
    def cost(self) -> float:
        """Blended-rate cost (default family). Retained for backward compatibility."""
        return (
            self.input * RATE_INPUT
            + self.output * RATE_OUTPUT
            + self.cache_create * RATE_CACHE_WRITE
            + self.cache_read * RATE_CACHE_READ
        ) / 1e6


# ---------------------------------------------------------------------------
# Session / Project dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """One JSONL transcript file."""

    file: str
    cwd: str
    usage: Usage
    by_model: dict[str, Usage] = field(default_factory=dict)
    by_day_model: dict[str, dict[str, Usage]] = field(default_factory=dict)

    @property
    def cost(self) -> float:
        """Per-family-accurate cost (Σ cost_of per family)."""
        return sum(cost_of(u, fam) for fam, u in self.by_model.items())


@dataclass
class Project:
    """A directory's worth of sessions, aggregated."""

    key: str  # display path (cwd) or encoded folder name
    usage: Usage = field(default_factory=Usage)
    sessions: list[Session] = field(default_factory=list)
    by_model: dict[str, Usage] = field(default_factory=dict)

    @property
    def cost(self) -> float:
        """Per-family-accurate cost (Σ cost_of per family)."""
        return sum(cost_of(u, fam) for fam, u in self.by_model.items())


# ---------------------------------------------------------------------------
# Daily aggregation
# ---------------------------------------------------------------------------


@dataclass
class DayUsage:
    """Token usage for a single local calendar day, broken out by model family."""

    day: str  # "YYYY-MM-DD" or "unknown"
    by_model: dict[str, Usage] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Total tokens across all families."""
        return sum(u.total for u in self.by_model.values())

    @property
    def cost(self) -> float:
        """Per-family-accurate cost (Σ cost_of per family)."""
        return sum(cost_of(u, fam) for fam, u in self.by_model.items())

    @property
    def client(self) -> float:
        """Cost after applying the markup multiplier."""
        return client_cost(self.cost)


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def _parse_file(path: Path) -> Session:
    usage = Usage(sessions=1)
    cwd: str | None = None
    by_model: dict[str, Usage] = {}
    by_day_model: dict[str, dict[str, Usage]] = {}
    try:
        with path.open("r", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd_val = obj.get("cwd")
                if isinstance(cwd_val, str):
                    cwd = cwd_val
                msg = obj.get("message")
                if isinstance(msg, dict):
                    u = msg.get("usage")
                    if isinstance(u, dict):
                        line_input = u.get("input_tokens") or 0
                        line_output = u.get("output_tokens") or 0
                        line_cache_create = u.get("cache_creation_input_tokens") or 0
                        line_cache_read = u.get("cache_read_input_tokens") or 0
                        usage.input += line_input
                        usage.output += line_output
                        usage.cache_create += line_cache_create
                        usage.cache_read += line_cache_read
                        # Per-model bucketing
                        model_id = msg.get("model")
                        fam = family_of(model_id if isinstance(model_id, str) else None)
                        if fam not in by_model:
                            by_model[fam] = Usage()
                        by_model[fam].input += line_input
                        by_model[fam].output += line_output
                        by_model[fam].cache_create += line_cache_create
                        by_model[fam].cache_read += line_cache_read
                        # Per-(day, family) bucketing — skip zero-usage lines
                        if line_input or line_output or line_cache_create or line_cache_read:
                            ts = obj.get("timestamp")
                            day = _local_day(ts if isinstance(ts, str) else None)
                            if day not in by_day_model:
                                by_day_model[day] = {}
                            if fam not in by_day_model[day]:
                                by_day_model[day][fam] = Usage()
                            by_day_model[day][fam].input += line_input
                            by_day_model[day][fam].output += line_output
                            by_day_model[day][fam].cache_create += line_cache_create
                            by_day_model[day][fam].cache_read += line_cache_read
    except OSError:
        pass
    return Session(
        file=str(path),
        cwd=cwd or path.parent.name,
        usage=usage,
        by_model=by_model,
        by_day_model=by_day_model,
    )


def _iter_files(projects_dir: Path) -> Iterable[Path]:
    if not projects_dir.is_dir():
        return
    for folder in projects_dir.iterdir():
        if not folder.is_dir():
            continue
        yield from folder.glob("*.jsonl")


# ---------------------------------------------------------------------------
# scan() — per-directory leaderboard (unchanged behaviour, gains by_model)
# ---------------------------------------------------------------------------


def scan(
    merge_by_cwd: bool = True,
    projects_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Project]:
    """Aggregate usage per directory.

    merge_by_cwd: group folders that resolve to the same working directory
    (covers worktrees and duplicate encoded folders). When False, each encoded
    folder is its own row.
    progress(done, total): optional callback for a loading indicator.
    """
    projects_dir = projects_dir or PROJECTS_DIR
    files = list(_iter_files(projects_dir))
    total = len(files)
    by_key: dict[str, Project] = {}
    for i, path in enumerate(files, start=1):
        session = _parse_file(path)
        key = session.cwd if merge_by_cwd else path.parent.name
        proj = by_key.get(key)
        if proj is None:
            proj = Project(key=key)
            by_key[key] = proj
        proj.usage.add(session.usage)
        proj.sessions.append(session)
        # Merge per-model usage into the project
        for fam, fam_usage in session.by_model.items():
            if fam not in proj.by_model:
                proj.by_model[fam] = Usage()
            proj.by_model[fam].input += fam_usage.input
            proj.by_model[fam].output += fam_usage.output
            proj.by_model[fam].cache_create += fam_usage.cache_create
            proj.by_model[fam].cache_read += fam_usage.cache_read
        if progress is not None:
            progress(i, total)
    projects = list(by_key.values())
    projects.sort(key=lambda p: p.usage.total, reverse=True)
    return projects


# ---------------------------------------------------------------------------
# scan_daily() — per-day, per-model breakdown
# ---------------------------------------------------------------------------


def scan_daily(
    projects_dir: Path | None = None,
    cwd: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[DayUsage]:
    """Aggregate token usage by local calendar day and model family.

    cwd: when given, include sessions whose transcript cwd is this directory or
         any directory nested beneath it (worktrees, .claude subdirs fold in);
         None → all directories (global scope).
    Sort: real days descending, "unknown" always last.
    """
    projects_dir = projects_dir or PROJECTS_DIR
    files = list(_iter_files(projects_dir))
    total = len(files)

    # day → family → Usage
    by_day: dict[str, dict[str, Usage]] = {}

    for i, path in enumerate(files, start=1):
        session = _parse_file(path)
        # Filter by scope when requested — the launch dir AND everything nested
        # beneath it (worktrees, .claude/agents) fold into the same daily rows.
        if not cwd_in_scope(session.cwd, cwd):
            if progress is not None:
                progress(i, total)
            continue
        # Merge per-(day, family) usage from the single parse pass
        for day, fam_map in session.by_day_model.items():
            if day not in by_day:
                by_day[day] = {}
            for fam, fam_usage in fam_map.items():
                if fam not in by_day[day]:
                    by_day[day][fam] = Usage()
                by_day[day][fam].input += fam_usage.input
                by_day[day][fam].output += fam_usage.output
                by_day[day][fam].cache_create += fam_usage.cache_create
                by_day[day][fam].cache_read += fam_usage.cache_read
        if progress is not None:
            progress(i, total)

    days = [DayUsage(day=day, by_model=fam_map) for day, fam_map in by_day.items()]

    # Sort: real days desc, "unknown" always last
    real = sorted((d for d in days if d.day != "unknown"), key=lambda d: d.day, reverse=True)
    unknown = [d for d in days if d.day == "unknown"]
    return real + unknown


def shorten(path: str) -> str:
    """Collapse the home directory to ``~`` for display."""
    if path.startswith(HOME):
        return "~" + path[len(HOME):]
    return path
