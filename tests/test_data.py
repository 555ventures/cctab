"""Data-layer unit tests: parsing, aggregation, cost math, merge-by-cwd.

AC-IDs are referenced in each test's docstring (the repo convention).
These exercise the numbers cctop exists to report, against synthetic
transcript dirs built in tmp_path — no dependency on the real ~/.claude.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cctop.data import (
    RATE_INPUT,
    RATE_OUTPUT,
    RATES,
    Usage,
    _local_day,
    client_cost,
    cost_of,
    family_of,
    scan,
    scan_daily,
    shorten,
)


def _write_session(folder: Path, name: str, cwd: str, rows: list[dict]) -> None:
    """Write a synthetic JSONL session transcript.

    Each dict in *rows* maps to one usage-bearing line.  Optional keys
    ``"timestamp"`` and ``"model"`` are lifted to the line's top level and
    ``message.model`` respectively; the remaining keys are treated as usage
    token counts (``input_tokens`` etc.) and placed under ``message.usage``.
    Rows omitting those keys behave exactly as before.
    """
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"cwd": cwd}]
    for row in rows:
        ts = row.get("timestamp")
        model = row.get("model")
        usage = {k: v for k, v in row.items() if k not in ("timestamp", "model")}
        entry: dict = {"message": {"usage": usage}}
        if ts is not None:
            entry["timestamp"] = ts
        if model is not None:
            entry["message"]["model"] = model
        lines.append(entry)
    (folder / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_usage_total_sums_all_token_classes() -> None:
    """AC-DATA-1: Usage.total sums input + output + cache write + cache read."""
    u = Usage(input=1, output=2, cache_create=4, cache_read=8)
    assert u.total == 15


def test_cost_blends_per_mtok_rates() -> None:
    """AC-DATA-2: cost applies per-class $/MTok rates, scaled by 1e6."""
    u = Usage(input=1_000_000, output=1_000_000)
    assert u.cost == RATE_INPUT + RATE_OUTPUT


def test_scan_aggregates_and_merges_by_cwd(tmp_path: Path) -> None:
    """AC-DATA-3: two encoded folders with the same cwd merge into one row."""
    root = tmp_path / "projects"
    _write_session(root / "enc-a", "s1.jsonl", "/work/proj", [{"input_tokens": 10}])
    _write_session(root / "enc-b", "s2.jsonl", "/work/proj", [{"output_tokens": 5}])

    merged = scan(merge_by_cwd=True, projects_dir=root)
    assert len(merged) == 1
    assert merged[0].key == "/work/proj"
    assert merged[0].usage.sessions == 2
    assert merged[0].usage.input == 10
    assert merged[0].usage.output == 5

    per_folder = scan(merge_by_cwd=False, projects_dir=root)
    assert len(per_folder) == 2


def test_scan_skips_malformed_lines(tmp_path: Path) -> None:
    """AC-DATA-4: invalid JSON lines are ignored, valid usage still counted."""
    root = tmp_path / "projects"
    folder = root / "enc"
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text(
        '{"cwd": "/p"}\nnot json\n{"message": {"usage": {"input_tokens": 7}}}\n'
    )
    projects = scan(projects_dir=root)
    assert projects[0].usage.input == 7


def test_shorten_collapses_home(monkeypatch) -> None:
    """AC-DATA-5: paths under $HOME render with a leading ~."""
    import cctop.data as data

    monkeypatch.setattr(data, "HOME", "/home/me")
    assert shorten("/home/me/Projects/x") == "~/Projects/x"
    assert shorten("/other/x") == "/other/x"


# ---------------------------------------------------------------------------
# AC-DATA-6 through AC-DATA-12 — daily-cost-by-model spec
# ---------------------------------------------------------------------------


def test_family_of_maps_by_substring() -> None:
    """AC-DATA-6: family_of maps model ids by case-insensitive substring match."""
    assert family_of("claude-opus-4-8") == "opus"
    assert family_of("claude-sonnet-4-6") == "sonnet"
    assert family_of("claude-fable-5") == "fable"
    assert family_of("claude-haiku-4-5") == "haiku"
    assert family_of("<synthetic>") == "synthetic"
    assert family_of("mystery-model") == "default"
    assert family_of(None) == "default"
    # Case-insensitive: uppercased variants must still resolve
    assert family_of("CLAUDE-OPUS-4-8") == "opus"
    assert family_of("CLAUDE-SONNET-4-6") == "sonnet"


def test_cost_of_uses_family_rates() -> None:
    """AC-DATA-7: cost_of prices usage with per-family rates, not the blended rate."""
    # 1M input tokens at opus rate
    u_opus_input = Usage(input=1_000_000)
    assert cost_of(u_opus_input, "opus") == pytest.approx(RATES["opus"].input)

    # Synthetic family must price to exactly $0 regardless of token count
    u_synth = Usage(input=1_000_000, output=1_000_000)
    assert cost_of(u_synth, "synthetic") == 0.0

    # Sonnet 1M output — must differ from opus (proving per-family, not blended)
    u_sonnet_out = Usage(output=1_000_000)
    assert cost_of(u_sonnet_out, "sonnet") == pytest.approx(RATES["sonnet"].output)
    assert cost_of(u_sonnet_out, "sonnet") != cost_of(u_sonnet_out, "opus")


def test_client_cost_applies_margin(monkeypatch) -> None:
    """AC-DATA-8: client_cost multiplies by MARGIN; unset MARGIN defaults to 1.0."""
    import cctop.data as data

    # With MARGIN=1.5
    monkeypatch.setattr(data, "MARGIN", 1.5)
    assert client_cost(10.0) == pytest.approx(15.0)

    # With MARGIN=1.0 (default)
    monkeypatch.setattr(data, "MARGIN", 1.0)
    assert client_cost(10.0) == pytest.approx(10.0)


def test_scan_daily_buckets_by_day_and_model(tmp_path: Path, monkeypatch) -> None:
    """AC-DATA-9: two sessions on the same cwd/day with different models merge into one DayUsage."""
    # Force local timezone to UTC so "2026-06-16T02:30:00Z" → day "2026-06-16"
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()

    root = tmp_path / "projects"
    _write_session(
        root / "enc-a",
        "s1.jsonl",
        "/work/proj",
        [
            {
                "timestamp": "2026-06-16T02:30:00Z",
                "model": "claude-opus-4-8",
                "input_tokens": 1_000_000,
            }
        ],
    )
    _write_session(
        root / "enc-b",
        "s2.jsonl",
        "/work/proj",
        [
            {
                "timestamp": "2026-06-16T10:00:00Z",
                "model": "claude-sonnet-4-6",
                "output_tokens": 1_000_000,
            }
        ],
    )

    days = scan_daily(projects_dir=root, cwd="/work/proj")

    assert len(days) == 1
    day = days[0]
    assert day.day == "2026-06-16"
    assert "opus" in day.by_model
    assert "sonnet" in day.by_model
    assert day.by_model["opus"].input == 1_000_000
    assert day.by_model["sonnet"].output == 1_000_000

    expected_cost = cost_of(Usage(input=1_000_000), "opus") + cost_of(
        Usage(output=1_000_000), "sonnet"
    )
    assert day.cost == pytest.approx(expected_cost)


def test_scan_daily_filters_by_cwd(tmp_path: Path, monkeypatch) -> None:
    """AC-DATA-10: scan_daily(cwd=X) includes only X days; cwd=None includes all."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()

    root = tmp_path / "projects"
    _write_session(
        root / "enc-a",
        "a.jsonl",
        "/work/a",
        [{"timestamp": "2026-06-15T08:00:00Z", "model": "claude-sonnet-4-6", "input_tokens": 10}],
    )
    _write_session(
        root / "enc-b",
        "b.jsonl",
        "/work/b",
        [{"timestamp": "2026-06-15T09:00:00Z", "model": "claude-haiku-4-5", "input_tokens": 20}],
    )

    # Filtered to /work/a — must contain only /work/a days
    days_a = scan_daily(projects_dir=root, cwd="/work/a")
    # There should be exactly one day and it must have data only from /work/a
    assert len(days_a) == 1
    total_tokens_a = sum(day.by_model[f].input for day in days_a for f in day.by_model)
    assert total_tokens_a == 10  # only /work/a's 10 tokens

    # Global (cwd=None) — must include days from both directories
    days_all = scan_daily(projects_dir=root, cwd=None)
    total_tokens_all = sum(
        getattr(day.by_model.get(f, Usage()), "input", 0)
        + getattr(day.by_model.get(f, Usage()), "output", 0)
        for day in days_all
        for f in day.by_model
    )
    assert total_tokens_all == 30  # 10 from /work/a + 20 from /work/b


def test_scan_daily_handles_bad_timestamp(tmp_path: Path, monkeypatch) -> None:
    """AC-DATA-11: missing/unparseable timestamps go to 'unknown' (last); Z-suffix parses fine."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()

    # _local_day must handle Z-suffix without raising and return a real day
    result = _local_day("2026-06-16T02:30:00Z")
    assert result != "unknown"
    assert result == "2026-06-16"

    # _local_day must return "unknown" for bad/missing input without raising
    assert _local_day(None) == "unknown"
    assert _local_day("not-a-date") == "unknown"

    # In scan_daily, a line with no timestamp still contributes tokens under "unknown"
    root = tmp_path / "projects"
    _write_session(
        root / "enc-a",
        "s1.jsonl",
        "/work/proj",
        [
            # Real day
            {"timestamp": "2026-06-16T02:30:00Z", "model": "claude-sonnet-4-6", "input_tokens": 5},
            # No timestamp → "unknown" day
            {"model": "claude-sonnet-4-6", "input_tokens": 3},
        ],
    )

    days = scan_daily(projects_dir=root, cwd="/work/proj")

    day_labels = [d.day for d in days]
    assert "2026-06-16" in day_labels
    assert "unknown" in day_labels

    # "unknown" must sort AFTER all real days
    real_indices = [i for i, d in enumerate(days) if d.day != "unknown"]
    unknown_indices = [i for i, d in enumerate(days) if d.day == "unknown"]
    assert all(r < u for r in real_indices for u in unknown_indices)

    # Tokens in "unknown" bucket are still counted
    unknown_day = next(d for d in days if d.day == "unknown")
    assert unknown_day.total > 0


def test_project_cost_sums_per_model(tmp_path: Path, monkeypatch) -> None:
    """AC-DATA-12: Project.cost sums per-family costs, not a blended rate on pooled tokens."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()

    root = tmp_path / "projects"
    _write_session(
        root / "enc-a",
        "s1.jsonl",
        "/work/proj",
        [
            # 1M opus output
            {
                "timestamp": "2026-06-15T08:00:00Z",
                "model": "claude-opus-4-8",
                "output_tokens": 1_000_000,
            },
            # 1M sonnet output
            {
                "timestamp": "2026-06-15T09:00:00Z",
                "model": "claude-sonnet-4-6",
                "output_tokens": 1_000_000,
            },
        ],
    )

    projects = scan(projects_dir=root)
    assert len(projects) == 1
    proj = projects[0]

    expected_cost = RATES["opus"].output + RATES["sonnet"].output  # each is $/MTok for 1M tokens
    # Project.cost must NOT be the blended rate on 2M output tokens
    blended_cost = proj.usage.cost  # RATE_OUTPUT * 2_000_000 / 1e6
    assert hasattr(proj, "cost"), "Project.cost property not found"
    assert proj.cost == pytest.approx(expected_cost)
    assert proj.cost != pytest.approx(blended_cost)


# ---------------------------------------------------------------------------
# AC-CFG-1..4 — per-directory margin config (.cctop)
# ---------------------------------------------------------------------------


def test_read_dir_margin_present(tmp_path: Path) -> None:
    """AC-CFG-1: read_dir_margin returns 2.0 when <D>/.cctop is {"margin": 2.0}."""
    from cctop.data import DIR_CONFIG_NAME, read_dir_margin

    cfg = tmp_path / DIR_CONFIG_NAME
    cfg.write_text(json.dumps({"margin": 2.0}))

    result = read_dir_margin(tmp_path)
    assert result == pytest.approx(2.0)


def test_read_dir_margin_absent_or_malformed(tmp_path: Path) -> None:
    """AC-CFG-2: read_dir_margin returns None for missing/malformed/out-of-range inputs."""
    from cctop.data import DIR_CONFIG_NAME, read_dir_margin

    # Case 1: no .cctop file → None, no raise
    assert read_dir_margin(tmp_path) is None

    cfg = tmp_path / DIR_CONFIG_NAME

    # Case 2: invalid JSON → None, no raise
    cfg.write_text("not json{")
    assert read_dir_margin(tmp_path) is None

    # Case 3: JSON object but no "margin" key → None
    cfg.write_text(json.dumps({"foo": 1}))
    assert read_dir_margin(tmp_path) is None

    # Case 4: margin is a numeric string, not a number → None
    cfg.write_text(json.dumps({"margin": "2.0"}))
    assert read_dir_margin(tmp_path) is None

    # Case 5: margin is a bool (True), not a number → None
    cfg.write_text(json.dumps({"margin": True}))
    assert read_dir_margin(tmp_path) is None

    # Case 6: margin is negative → None
    cfg.write_text(json.dumps({"margin": -1.0}))
    assert read_dir_margin(tmp_path) is None

    # Case 7: margin is Infinity → None.
    # Python's json module rejects "Infinity" as invalid JSON (strict), so
    # json.loads raises JSONDecodeError — read_dir_margin must swallow it.
    cfg.write_text('{"margin": Infinity}')
    result = read_dir_margin(tmp_path)
    # json.JSONDecodeError is swallowed; must return None without raising
    assert result is None


def test_write_dir_margin_roundtrip(tmp_path: Path) -> None:
    """AC-CFG-3: write_dir_margin creates .cctop with correct value; round-trips to 1.5."""
    from cctop.data import DIR_CONFIG_NAME, read_dir_margin, write_dir_margin

    returned = write_dir_margin(tmp_path, 1.5)

    # Must return True on success
    assert returned is True

    # The file must exist
    cfg = tmp_path / DIR_CONFIG_NAME
    assert cfg.exists()

    # The file content must parse back to {"margin": 1.5}
    with cfg.open() as fh:
        parsed = json.load(fh)
    assert parsed == {"margin": 1.5}

    # round-trip via read_dir_margin
    assert read_dir_margin(tmp_path) == pytest.approx(1.5)

    # No orphaned temp files (.cctop.*.tmp) must remain
    orphans = list(tmp_path.glob(".cctop.*.tmp"))
    assert orphans == [], f"Orphaned temp files found: {orphans}"


def test_write_dir_margin_readonly_returns_false(tmp_path: Path) -> None:
    """AC-CFG-4: write_dir_margin returns False for read-only dir; no raise; no temp file."""
    import os

    from cctop.data import DIR_CONFIG_NAME, write_dir_margin

    # Create a pre-existing .cctop so we can verify it is left intact
    existing_cfg = tmp_path / DIR_CONFIG_NAME
    existing_cfg.write_text(json.dumps({"margin": 9.9}))

    # Make the directory read-only (no write permission)
    os.chmod(tmp_path, 0o555)
    try:
        result = write_dir_margin(tmp_path, 2.0)
    finally:
        # Restore permissions so pytest cleanup can remove tmp_path
        os.chmod(tmp_path, 0o755)

    # Must return False, never raise
    assert result is False

    # The existing .cctop must remain intact (not overwritten or corrupted)
    assert existing_cfg.exists()
    with existing_cfg.open() as fh:
        intact = json.load(fh)
    assert intact == {"margin": 9.9}

    # No orphaned temp files
    orphans = list(tmp_path.glob(".cctop.*.tmp"))
    assert orphans == [], f"Orphaned temp files found: {orphans}"
