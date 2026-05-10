"""Tests for the security master sync.

The pure-functional pieces (``compute_changes``, ``merge_master``) are
tested directly with synthesized DataFrames; the orchestrator is tested
end-to-end with a faked Massive client so no network calls are issued.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from agora.download.security_master import (
    CHANGE_COLUMNS,
    MASTER_COLUMNS,
    PartialUniverseError,
    _atomic_write_parquet,
    _EventCache,
    _normalize_event_date,
    compute_changes,
    fetch_universe,
    merge_master,
    sync_security_master,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _row(
    composite_figi="BBG000B9XRY4",
    ticker="AAPL",
    name="Apple Inc.",
    primary_exchange="XNAS",
    type_="CS",
    active=True,
    cik="0000320193",
    locale="us",
    currency_name="usd",
    share_class_figi="BBG001S5N8V8",
    market="stocks",
    polygon_last_updated_utc=None,
    identity_source="composite_figi",
):
    return {
        "composite_figi": composite_figi,
        "share_class_figi": share_class_figi,
        "ticker": ticker,
        "name": name,
        "cik": cik,
        "market": market,
        "type": type_,
        "locale": locale,
        "primary_exchange": primary_exchange,
        "currency_name": currency_name,
        "active": active,
        "polygon_last_updated_utc": polygon_last_updated_utc,
        "identity_source": identity_source,
    }


def _new_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a today-style frame (no first_seen_at/last_seen_at/as_of)."""
    return pd.DataFrame(rows)


def _master_frame(rows: list[dict], detected_at: datetime) -> pd.DataFrame:
    """Build a prior-master-style frame with timestamp columns populated."""
    df = pd.DataFrame(rows)
    df["first_seen_at"] = detected_at
    df["last_seen_at"] = detected_at
    df["as_of"] = detected_at
    return df[list(MASTER_COLUMNS)]


# ── compute_changes ─────────────────────────────────────────────────


def test_bootstrap_emits_added_for_every_row():
    new = _new_frame([_row(), _row(composite_figi="BBG_2", ticker="MSFT")])
    changes = compute_changes(
        pd.DataFrame(), new, run_id="r1", detected_at=datetime.now(UTC)
    )
    assert len(changes) == 2
    assert (changes["change_type"] == "added").all()
    assert (changes["source"] == "bootstrap").all()
    assert set(changes.columns) == set(CHANGE_COLUMNS)


def test_idempotent_no_changes_when_universe_identical():
    detected_at = datetime.now(UTC)
    rows = [_row(), _row(composite_figi="BBG_2", ticker="MSFT")]
    prev = _master_frame(rows, detected_at)
    new = _new_frame(rows)
    changes = compute_changes(prev, new, run_id="r2", detected_at=detected_at)
    assert changes.empty


def test_added_emits_one_row_with_list_tickers_source():
    detected_at = datetime.now(UTC)
    prev = _master_frame([_row()], detected_at)
    new = _new_frame([_row(), _row(composite_figi="BBG_NEW", ticker="NEW")])
    changes = compute_changes(prev, new, run_id="r3", detected_at=detected_at)
    added = changes[changes["change_type"] == "added"]
    assert len(added) == 1
    assert added.iloc[0]["composite_figi"] == "BBG_NEW"
    assert added.iloc[0]["source"] == "list_tickers"


def test_deactivated_when_active_row_drops_from_universe():
    detected_at = datetime.now(UTC)
    prev = _master_frame(
        [_row(), _row(composite_figi="BBG_GONE", ticker="GONE", active=True)],
        detected_at,
    )
    new = _new_frame([_row()])
    changes = compute_changes(prev, new, run_id="r4", detected_at=detected_at)
    deact = changes[changes["change_type"] == "deactivated"]
    assert len(deact) == 1
    assert deact.iloc[0]["composite_figi"] == "BBG_GONE"
    assert deact.iloc[0]["old_value"] == "True"
    assert deact.iloc[0]["new_value"] == "False"


def test_no_event_when_inactive_row_stays_absent():
    """An inactive prior row that's still absent should NOT re-emit."""
    detected_at = datetime.now(UTC)
    prev = _master_frame(
        [_row(), _row(composite_figi="BBG_DEAD", ticker="DEAD", active=False)],
        detected_at,
    )
    new = _new_frame([_row()])
    changes = compute_changes(prev, new, run_id="r5", detected_at=detected_at)
    assert changes.empty


def test_reactivated_when_inactive_row_returns():
    detected_at = datetime.now(UTC)
    prev = _master_frame(
        [_row(composite_figi="BBG_X", ticker="X", active=False)], detected_at
    )
    new = _new_frame([_row(composite_figi="BBG_X", ticker="X", active=True)])
    changes = compute_changes(prev, new, run_id="r6", detected_at=detected_at)
    react = changes[changes["change_type"] == "reactivated"]
    assert len(react) == 1
    assert react.iloc[0]["composite_figi"] == "BBG_X"


def test_field_changed_emits_one_row_per_changed_field():
    detected_at = datetime.now(UTC)
    prev = _master_frame([_row(name="OLD", primary_exchange="XNAS")], detected_at)
    new = _new_frame([_row(name="NEW", primary_exchange="ARCX")])
    changes = compute_changes(prev, new, run_id="r7", detected_at=detected_at)
    fc = changes[changes["change_type"] == "field_changed"]
    fields = set(fc["field_name"])
    assert fields == {"name", "primary_exchange"}
    name_row = fc[fc["field_name"] == "name"].iloc[0]
    assert name_row["old_value"] == "OLD"
    assert name_row["new_value"] == "NEW"


def test_ticker_change_emits_field_changed_on_ticker():
    detected_at = datetime.now(UTC)
    prev = _master_frame([_row(ticker="FB")], detected_at)
    new = _new_frame([_row(ticker="META")])
    changes = compute_changes(prev, new, run_id="r8", detected_at=detected_at)
    fc = changes[
        (changes["change_type"] == "field_changed")
        & (changes["field_name"] == "ticker")
    ]
    assert len(fc) == 1
    assert fc.iloc[0]["old_value"] == "FB"
    assert fc.iloc[0]["new_value"] == "META"


# ── merge_master ────────────────────────────────────────────────────


def test_merge_master_bootstrap_stamps_all_timestamps():
    detected_at = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    new = _new_frame([_row()])
    merged = merge_master(pd.DataFrame(), new, detected_at=detected_at)
    assert len(merged) == 1
    row = merged.iloc[0]
    assert row["first_seen_at"] == detected_at
    assert row["last_seen_at"] == detected_at
    assert row["as_of"] == detected_at
    assert list(merged.columns) == list(MASTER_COLUMNS)


def test_merge_master_carries_first_seen_at_advances_last_seen_at():
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    t2 = datetime(2025, 6, 1, tzinfo=UTC)
    prev = _master_frame([_row()], t1)
    new = _new_frame([_row(name="Apple Computer")])  # name updated today
    merged = merge_master(prev, new, detected_at=t2)
    row = merged.iloc[0]
    assert row["first_seen_at"] == t1   # carried over
    assert row["last_seen_at"] == t2     # advanced to today
    assert row["as_of"] == t2
    assert row["name"] == "Apple Computer"


def test_merge_master_marks_missing_rows_inactive():
    t1 = datetime(2025, 1, 1, tzinfo=UTC)
    t2 = datetime(2025, 6, 1, tzinfo=UTC)
    prev = _master_frame(
        [_row(), _row(composite_figi="BBG_GONE", ticker="GONE", active=True)],
        t1,
    )
    new = _new_frame([_row()])
    merged = merge_master(prev, new, detected_at=t2)
    assert len(merged) == 2
    gone = merged[merged["composite_figi"] == "BBG_GONE"].iloc[0]
    assert gone["active"] is False or gone["active"] == 0
    assert gone["as_of"] == t2
    # last_seen_at NOT advanced — we did not see it today
    assert gone["last_seen_at"] == t1


# ── sync_security_master orchestrator ───────────────────────────────


class _FakeTicker:
    def __init__(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)


def _fake_client(active_rows: list[dict], events_by_ticker: dict | None = None):
    """Return a MagicMock RESTClient that yields the given rows from
    ``list_tickers`` and (optionally) per-ticker rename events from
    ``get_ticker_events``. Tracks call counts on ``client.get_ticker_events``.
    """
    client = MagicMock()

    def _list_tickers(market="stocks", type=None, active=True, limit=1000):
        for r in active_rows:
            if r["market"] != market:
                continue
            if type is not None and r["type"] != type:
                continue
            if active and not r.get("active", True):
                continue
            yield _FakeTicker(**{k: v for k, v in r.items() if k != "identity_source"})

    client.list_tickers.side_effect = _list_tickers

    events_by_ticker = events_by_ticker or {}

    class _EventsResult:
        def __init__(self, events: list):
            self.events = events
            self.composite_figi = None
            self.name = None
            self.cik = None

    def _get_ticker_events(ticker: str):
        return _EventsResult(events_by_ticker.get(ticker, []))

    client.get_ticker_events.side_effect = _get_ticker_events
    return client


@pytest.fixture
def fake_client_factory(monkeypatch):
    """Patch ``sync_security_master``'s ``RESTClient`` and ``time.sleep``.

    Returns a callable that installs a client returning the given rows.
    Optionally pass ``events_by_ticker`` to seed rename history.
    """
    def _install(rows: list[dict], events_by_ticker: dict | None = None):
        client = _fake_client(rows, events_by_ticker=events_by_ticker)
        monkeypatch.setattr(
            "agora.download.security_master.RESTClient",
            lambda api_key=None: client,
        )
        monkeypatch.setattr("agora.download.security_master.time.sleep", lambda _s: None)
        return client

    return _install


def test_sync_bootstrap_writes_master_and_changes(
    tmp_path: Path, fake_client_factory
):
    rows = [
        _row(),
        _row(composite_figi="BBG_2", ticker="MSFT", name="Microsoft"),
        _row(composite_figi="BBG_3", ticker="SPY", type_="ETF", name="SPDR S&P 500"),
    ]
    fake_client_factory(rows)

    result = sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )
    assert result.succeeded

    master_path = tmp_path / "security_master.parquet"
    changes_path = tmp_path / "security_master_changes.parquet"
    assert master_path.exists()
    assert changes_path.exists()

    master = pd.read_parquet(master_path)
    assert len(master) == 3
    assert set(master["ticker"]) == {"AAPL", "MSFT", "SPY"}
    # Bootstrap: every row has first_seen_at == as_of
    assert (master["first_seen_at"] == master["as_of"]).all()
    assert (master["active"]).all()

    changes = pd.read_parquet(changes_path)
    assert len(changes) == 3
    assert (changes["change_type"] == "added").all()
    assert (changes["source"] == "bootstrap").all()
    # All changes share one run_id
    assert changes["run_id"].nunique() == 1


def test_sync_idempotent_on_unchanged_universe(
    tmp_path: Path, fake_client_factory
):
    rows = [_row(), _row(composite_figi="BBG_2", ticker="MSFT", name="Microsoft")]
    fake_client_factory(rows)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )
    first_changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")

    # Re-install fresh fake (same rows) and resync
    fake_client_factory(rows)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )
    second_changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")

    # Idempotent — no new rows appended
    assert len(second_changes) == len(first_changes)


def test_sync_detects_field_change_on_second_run(
    tmp_path: Path, fake_client_factory
):
    rows = [_row(name="Apple Inc.")]
    fake_client_factory(rows)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )

    # Second run with a renamed company name
    rows = [_row(name="Apple Computer Inc.")]
    fake_client_factory(rows)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )

    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    name_changes = changes[
        (changes["change_type"] == "field_changed")
        & (changes["field_name"] == "name")
    ]
    assert len(name_changes) == 1
    assert name_changes.iloc[0]["old_value"] == "Apple Inc."
    assert name_changes.iloc[0]["new_value"] == "Apple Computer Inc."


def test_sync_writes_dated_snapshot_when_enabled(
    tmp_path: Path, fake_client_factory
):
    fake_client_factory([_row()])
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=True,
    )
    snap_dir = tmp_path / "snapshots"
    assert snap_dir.exists()
    assert any(snap_dir.glob("tickers_*.parquet"))


def test_backfill_triggered_on_any_field_change_not_just_ticker(
    tmp_path: Path, fake_client_factory
):
    """Enhancement (a): a non-ticker field_changed should still pull events."""
    fake_client_factory([_row(name="Apple Inc.")])
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )

    # Now change name only (not ticker); fake events present for AAPL
    events = {"AAPL": [
        {"type": "ticker_change", "date": "2010-01-01",
         "ticker_change": {"ticker": "AAPL", "composite_figi": "BBG000B9XRY4"}},
    ]}
    client = fake_client_factory(
        [_row(name="Apple Computer Inc.")], events_by_ticker=events
    )
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=True,
        write_dated_snapshot=False,
    )
    # Events API was called for the renamed ticker
    called_tickers = {c.args[0] for c in client.get_ticker_events.call_args_list}
    assert "AAPL" in called_tickers

    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    assert len(renamed) == 1
    assert renamed.iloc[0]["event_date"] == "2010-01-01"


def test_full_event_backfill_pulls_for_every_master_identity(
    tmp_path: Path, fake_client_factory
):
    """Enhancement (b): --full-event-backfill iterates every identity."""
    rows = [
        _row(),
        _row(composite_figi="BBG_2", ticker="MSFT", name="Microsoft"),
    ]
    events = {
        "AAPL": [{"type": "ticker_change", "date": "2010-01-01",
                  "ticker_change": {"ticker": "AAPL", "composite_figi": "BBG000B9XRY4"}}],
        "MSFT": [{"type": "ticker_change", "date": "1990-01-01",
                  "ticker_change": {"ticker": "MSFT", "composite_figi": "BBG_2"}}],
    }
    client = fake_client_factory(rows, events_by_ticker=events)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,        # turn off normal triggers
        full_event_backfill=True,     # but force full sweep
        write_dated_snapshot=False,
    )

    called = {c.args[0] for c in client.get_ticker_events.call_args_list}
    assert called == {"AAPL", "MSFT"}

    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    assert len(renamed) == 2  # one per ticker


def test_event_cache_short_circuits_api_call(
    tmp_path: Path, fake_client_factory
):
    """Enhancement (c): if ticker_events.parquet has the ticker, skip API."""
    # Pre-seed ticker_events.parquet with AAPL's history
    cache = pd.DataFrame([
        {
            "ticker": "AAPL",
            "composite_figi": "BBG000B9XRY4",
            "cik": "0000320193",
            "name": "Apple Inc.",
            "valid_from": pd.Timestamp("2010-01-01"),
            "valid_to": pd.NaT,
            "is_current": True,
        }
    ])
    cache.to_parquet(tmp_path / "ticker_events.parquet", index=False)

    client = fake_client_factory(
        [_row()],
        # Empty events_by_ticker — if API were called it would return nothing
        events_by_ticker={},
    )
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )

    # API should NOT have been called for AAPL (cache hit)
    called = {c.args[0] for c in client.get_ticker_events.call_args_list}
    assert "AAPL" not in called

    # But the rename row was still emitted, sourced from the cache
    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    assert len(renamed) == 1
    assert renamed.iloc[0]["source"] == "ticker_events_cache"


def test_indices_filtered_to_csv_allowlist(
    tmp_path: Path, fake_client_factory
):
    """Only tickers in the CSV allowlist land in the master from the indices pull."""
    # Write a small allowlist CSV
    allowlist_csv = tmp_path / "indices_included.csv"
    allowlist_csv.write_text(
        "ticker,name,type\n"
        "I:SPX,S&P 500,Broad\n"
        "I:VIX,Vol,Macro\n"
    )

    rows = [
        _row(),  # AAPL stocks (sanity row)
        _row(
            composite_figi=None, ticker="I:SPX", market="indices",
            type_=None, name="S&P 500", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
        _row(
            composite_figi=None, ticker="I:VIX", market="indices",
            type_=None, name="Vol", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
        _row(
            composite_figi=None, ticker="I:NQDMASIA4520LMT", market="indices",
            type_=None, name="Random Nasdaq sub-sub-index",
            primary_exchange=None, cik=None, share_class_figi=None,
        ),
    ]
    fake_client_factory(rows)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
        index_allowlist_path=allowlist_csv,
    )
    master = pd.read_parquet(tmp_path / "security_master.parquet")
    indices = set(master.loc[master["market"] == "indices", "ticker"])
    assert indices == {"I:SPX", "I:VIX"}
    assert "I:NQDMASIA4520LMT" not in indices


def test_indices_skipped_entirely_when_allowlist_csv_missing(
    tmp_path: Path, fake_client_factory
):
    """Missing CSV → empty allowlist → no indices in the master, but no fatal error."""
    rows = [
        _row(),  # one stocks row
        _row(
            composite_figi=None, ticker="I:SPX", market="indices",
            type_=None, name="S&P 500", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
    ]
    fake_client_factory(rows)
    missing_path = tmp_path / "no_such_file.csv"

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
        index_allowlist_path=missing_path,
    )
    master = pd.read_parquet(tmp_path / "security_master.parquet")
    assert (master["market"] == "indices").sum() == 0
    assert "AAPL" in set(master["ticker"])  # stocks still pulled


def test_index_allowlist_csv_handles_bom_and_case(
    tmp_path: Path, fake_client_factory
):
    """CSV with UTF-8 BOM and mixed case tickers must still match."""
    allowlist_csv = tmp_path / "indices_included.csv"
    # Leading BOM (﻿) — pandas read_csv handles utf-8-sig automatically
    allowlist_csv.write_bytes(b"\xef\xbb\xbfticker,name\ni:spx,S&P 500\n")

    rows = [
        _row(
            composite_figi=None, ticker="I:SPX", market="indices",
            type_=None, name="S&P 500", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
    ]
    fake_client_factory(rows)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
        index_allowlist_path=allowlist_csv,
    )
    master = pd.read_parquet(tmp_path / "security_master.parquet")
    indices = set(master.loc[master["market"] == "indices", "ticker"])
    assert indices == {"I:SPX"}


def test_event_backfill_skips_non_stocks_tickers(
    tmp_path: Path, fake_client_factory
):
    """Polygon's events endpoint is stocks-only — fx and indices must be skipped.

    Without this filter, bootstrap wastes hours of API calls on 404s
    for indices (Polygon has ~13K of them; 404 is the only response).
    """
    rows = [
        _row(),  # AAPL stocks
        _row(
            composite_figi=None, ticker="C:EURUSD", market="fx",
            type_=None, name="Euro / USD", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
        _row(
            composite_figi=None, ticker="I:SPX", market="indices",
            type_=None, name="S&P 500", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
    ]
    events = {
        "AAPL": [{"type": "ticker_change", "date": "2010-01-01",
                  "ticker_change": {"ticker": "AAPL", "composite_figi": "BBG000B9XRY4"}}],
    }
    client = fake_client_factory(rows, events_by_ticker=events)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=True,
        full_event_backfill=True,  # would otherwise iterate all 3 tickers
        write_dated_snapshot=False,
    )
    called = {c.args[0] for c in client.get_ticker_events.call_args_list}
    assert "AAPL" in called
    assert "C:EURUSD" not in called  # fx skipped
    assert "I:SPX" not in called      # indices skipped


def test_event_cache_hits_no_rename_stub_skips_api(
    tmp_path: Path, fake_client_factory
):
    """A ticker with a stub row (valid_from=NaT) is a cache hit, not a miss.

    The existing download_ticker_events flow writes a stub for tickers
    with no rename history. We must treat that as 'already checked, no
    history' rather than 'unchecked, hit the API'.
    """
    cache = pd.DataFrame([
        {
            "ticker": "AAPL",
            "composite_figi": "BBG000B9XRY4",
            "cik": "0000320193",
            "name": "Apple Inc.",
            "valid_from": pd.NaT,  # stub: no rename history
            "valid_to": pd.NaT,
            "is_current": True,
        }
    ])
    cache.to_parquet(tmp_path / "ticker_events.parquet", index=False)

    client = fake_client_factory([_row()], events_by_ticker={})
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )

    called = {c.args[0] for c in client.get_ticker_events.call_args_list}
    assert "AAPL" not in called  # stub hit → no API call


def test_within_run_dedupe_when_two_tickers_share_composite_figi(
    tmp_path: Path, fake_client_factory
):
    """Looking up FB and META (same figi) shouldn't double-emit the chain.

    Both tickers in the same sync would each pull the same rename
    history for the shared composite_figi. Without intra-run dedupe
    the change log accumulates duplicate rows.
    """
    cache = pd.DataFrame([
        {"ticker": "FB", "composite_figi": "BBG_META",
         "valid_from": pd.Timestamp("2012-05-18"), "valid_to": pd.NaT,
         "is_current": False, "cik": "1326801", "name": "Meta Platforms, Inc."},
        {"ticker": "META", "composite_figi": "BBG_META",
         "valid_from": pd.Timestamp("2022-06-09"), "valid_to": pd.NaT,
         "is_current": True, "cik": "1326801", "name": "Meta Platforms, Inc."},
    ])
    cache.to_parquet(tmp_path / "ticker_events.parquet", index=False)

    rows = [
        _row(composite_figi="BBG_META", ticker="FB"),
        _row(composite_figi="BBG_META", ticker="META"),
    ]
    fake_client_factory(rows, events_by_ticker={})
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )
    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    # Exactly one row per (figi, event_date, new_value) — not two
    assert len(renamed) == 2  # FB@2012-05-18 + META@2022-06-09
    assert (renamed["composite_figi"] == "BBG_META").all()
    keys = set(zip(
        renamed["composite_figi"], renamed["event_date"], renamed["new_value"], strict=False
    ))
    assert keys == {
        ("BBG_META", "2012-05-18", "FB"),
        ("BBG_META", "2022-06-09", "META"),
    }


def test_cache_lookup_prefers_is_current_when_ticker_reused(
    tmp_path: Path, fake_client_factory
):
    """When a ticker has been used by multiple securities, prefer the current one."""
    # META: previously a Roundhill ETF (BBG_RH), then renamed to METV.
    # Now META is Facebook (BBG_FB) — the current owner.
    cache = pd.DataFrame([
        {"ticker": "META", "composite_figi": "BBG_RH",
         "valid_from": pd.Timestamp("2021-06-30"),
         "valid_to": pd.Timestamp("2022-01-31"),
         "is_current": False, "cik": "X", "name": "Roundhill"},
        {"ticker": "METV", "composite_figi": "BBG_RH",
         "valid_from": pd.Timestamp("2022-01-31"), "valid_to": pd.NaT,
         "is_current": True, "cik": "X", "name": "Roundhill"},
        {"ticker": "FB", "composite_figi": "BBG_FB",
         "valid_from": pd.Timestamp("2012-05-18"),
         "valid_to": pd.Timestamp("2022-06-09"),
         "is_current": False, "cik": "Y", "name": "Meta Platforms"},
        {"ticker": "META", "composite_figi": "BBG_FB",
         "valid_from": pd.Timestamp("2022-06-09"), "valid_to": pd.NaT,
         "is_current": True, "cik": "Y", "name": "Meta Platforms"},
    ])
    cache.to_parquet(tmp_path / "ticker_events.parquet", index=False)

    rows = [_row(composite_figi="BBG_FB", ticker="META")]
    fake_client_factory(rows, events_by_ticker={})
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )
    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    # Cache lookup for META should resolve to BBG_FB (current),
    # emitting Facebook's chain — not Roundhill's.
    figis = set(renamed["composite_figi"])
    assert figis == {"BBG_FB"}


def test_full_backfill_is_idempotent_no_duplicate_rename_rows(
    tmp_path: Path, fake_client_factory
):
    """Re-running --full-event-backfill must not append duplicate ticker_renamed rows."""
    events = {"AAPL": [
        {"type": "ticker_change", "date": "2010-01-01",
         "ticker_change": {"ticker": "AAPL", "composite_figi": "BBG000B9XRY4"}},
    ]}

    fake_client_factory([_row()], events_by_ticker=events)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )
    first = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    first_renames = (first["change_type"] == "ticker_renamed").sum()
    assert first_renames == 1

    fake_client_factory([_row()], events_by_ticker=events)
    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )
    second = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    second_renames = (second["change_type"] == "ticker_renamed").sum()
    # Same count — duplicate dropped
    assert second_renames == first_renames


def test_sync_filters_fx_to_usd_quoted_pairs_only(
    tmp_path: Path, fake_client_factory
):
    """fx universe should drop non-USD-quoted pairs (e.g. C:EURGBP)."""
    rows = [
        _row(),  # one stocks row so we have a valid baseline
        _row(
            composite_figi=None, ticker="C:EURUSD", market="fx",
            type_=None, name="Euro / US Dollar", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
        _row(
            composite_figi=None, ticker="C:USDJPY", market="fx",
            type_=None, name="US Dollar / Japanese Yen", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
        _row(
            composite_figi=None, ticker="C:EURGBP", market="fx",
            type_=None, name="Euro / British Pound", primary_exchange=None,
            cik=None, share_class_figi=None,
        ),
    ]
    fake_client_factory(rows)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        write_dated_snapshot=False,
    )
    master = pd.read_parquet(tmp_path / "security_master.parquet")
    fx_tickers = set(master.loc[master["market"] == "fx", "ticker"])
    assert fx_tickers == {"C:EURUSD"}
    assert "C:USDJPY" not in fx_tickers  # USD as base, not quote
    assert "C:EURGBP" not in fx_tickers  # neither side is USD


# ── Regression tests for high-impact fixes (atomic write, partial pull,
#    O(1) cache, event_date normalization) ────────────────────────────


def test_atomic_write_leaves_no_tmp_file_on_success(tmp_path: Path):
    """After a successful atomic write, only the final file should exist."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    target = tmp_path / "x.parquet"
    _atomic_write_parquet(df, target)
    assert target.exists()
    assert not (tmp_path / "x.parquet.tmp").exists()


def test_atomic_write_preserves_original_on_write_failure(
    tmp_path: Path, monkeypatch
):
    """If the body of the write raises, the prior file must be untouched."""
    target = tmp_path / "x.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(target, index=False, engine="pyarrow")
    original_bytes = target.read_bytes()

    def _explode(*_a, **_k):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _explode)
    with pytest.raises(RuntimeError, match="simulated"):
        _atomic_write_parquet(pd.DataFrame({"a": [9]}), target)

    # Original is untouched
    assert target.read_bytes() == original_bytes


def test_fetch_universe_strict_raises_on_partial_failure(fake_client_factory):
    """A failed (market, type) pull aborts in strict mode (default)."""
    client = fake_client_factory([_row()])

    # Patch list_tickers to raise for one type
    original = client.list_tickers.side_effect

    def _flaky(*args, **kwargs):
        if kwargs.get("type") == "ETF":
            raise RuntimeError("simulated 503")
        return original(*args, **kwargs)

    client.list_tickers.side_effect = _flaky

    with pytest.raises(PartialUniverseError, match="failed segment"):
        fetch_universe(client, strict=True)


def test_fetch_universe_non_strict_continues_on_partial_failure(
    fake_client_factory,
):
    """With strict=False, partial failure logs and returns what it has."""
    client = fake_client_factory([_row()])
    original = client.list_tickers.side_effect

    def _flaky(*args, **kwargs):
        if kwargs.get("type") == "ETF":
            raise RuntimeError("simulated 503")
        return original(*args, **kwargs)

    client.list_tickers.side_effect = _flaky
    df = fetch_universe(client, strict=False)
    # AAPL is type=CS, not the failed segment, so it should still appear
    assert "AAPL" in set(df["ticker"])


def test_event_cache_lookup_is_o1_against_large_frame():
    """_EventCache is dict-backed; lookups don't scan the input frame."""
    # 1000 distinct tickers; only one renamed.
    rows = [
        {"ticker": f"T{i:04d}", "composite_figi": f"FIGI{i:04d}",
         "valid_from": pd.NaT, "valid_to": pd.NaT, "is_current": True}
        for i in range(1000)
    ]
    rows.append({
        "ticker": "META", "composite_figi": "BBG_META",
        "valid_from": pd.Timestamp("2022-06-09"), "valid_to": pd.NaT,
        "is_current": True,
    })
    rows.append({
        "ticker": "FB", "composite_figi": "BBG_META",
        "valid_from": pd.Timestamp("2012-05-18"),
        "valid_to": pd.Timestamp("2022-06-09"), "is_current": False,
    })
    df = pd.DataFrame(rows)
    cache = _EventCache(df)

    assert "META" in cache
    assert "T0500" in cache
    assert "ZZZZ" not in cache

    # META → Facebook chain (preferred is_current=True wins)
    events = cache.events_for("META")
    assert events is not None
    dates = sorted(e["date"] for e in events)
    assert dates == ["2012-05-18", "2022-06-09"]

    # Stub ticker — in cache, but no events
    assert cache.events_for("T0500") == []

    # Unknown ticker
    assert cache.events_for("ZZZZ") is None


def test_normalize_event_date_handles_iso_with_time_and_date_only():
    """Both API-style ISO datetime and bare date should normalize identically."""
    assert _normalize_event_date("2022-06-09") == "2022-06-09"
    assert _normalize_event_date("2022-06-09T00:00:00") == "2022-06-09"
    assert _normalize_event_date("2022-06-09T00:00:00Z") == "2022-06-09"
    assert _normalize_event_date(pd.Timestamp("2022-06-09")) == "2022-06-09"
    assert _normalize_event_date(None) is None
    assert _normalize_event_date(pd.NaT) is None
    assert _normalize_event_date("") is None


def test_event_date_format_consistent_across_cache_and_api(
    tmp_path: Path, fake_client_factory
):
    """Cache and API paths both produce YYYY-MM-DD; dedupe doesn't double-emit.

    Cache event source returns from valid_from (Timestamp). API source
    might return ISO datetime with time component. Both must normalize
    to identical string so the (figi, date, ticker) dedupe key matches.
    """
    # Pre-seed cache with a META rename event
    cache_csv = pd.DataFrame([
        {"ticker": "FB", "composite_figi": "BBG_META",
         "valid_from": pd.Timestamp("2012-05-18"),
         "valid_to": pd.Timestamp("2022-06-09"),
         "is_current": False, "cik": "1326801", "name": "Meta"},
        {"ticker": "META", "composite_figi": "BBG_META",
         "valid_from": pd.Timestamp("2022-06-09"), "valid_to": pd.NaT,
         "is_current": True, "cik": "1326801", "name": "Meta"},
    ])
    cache_csv.to_parquet(tmp_path / "ticker_events.parquet", index=False)

    # API would return META events with full ISO datetime format
    rows = [_row(composite_figi="BBG_META", ticker="META")]
    api_events = {
        "META": [
            {"type": "ticker_change",
             "date": "2022-06-09T00:00:00Z",  # ← time component included
             "ticker_change": {"ticker": "META", "composite_figi": "BBG_META"}},
            {"type": "ticker_change",
             "date": "2012-05-18T00:00:00Z",
             "ticker_change": {"ticker": "FB", "composite_figi": "BBG_META"}},
        ]
    }
    fake_client_factory(rows, events_by_ticker=api_events)

    sync_security_master(
        output_dir=tmp_path,
        api_key="dummy",
        backfill_events=False,
        full_event_backfill=True,
        write_dated_snapshot=False,
    )
    changes = pd.read_parquet(tmp_path / "security_master_changes.parquet")
    renamed = changes[changes["change_type"] == "ticker_renamed"]
    # Both paths produced "2022-06-09" identically; one row, not two
    assert (renamed["event_date"] == "2022-06-09").sum() == 1
    assert (renamed["event_date"] == "2012-05-18").sum() == 1
