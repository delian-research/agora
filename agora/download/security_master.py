"""Security master sync: parquet snapshot + append-only change log.

This module owns the "what securities exist and what do they look like"
slice of the data store. Each sync produces:

- ``data/reference/security_master.parquet`` — current state, one row per
  ``composite_figi``. Overwritten in place each run.
- ``data/reference/security_master_changes.parquet`` — append-only log
  of every detected change since the master was first built. Every row
  has a ``run_id`` (one UUID per sync invocation) so a single run's
  effects are easy to recover.

Identity model
~~~~~~~~~~~~~~
Primary identity is Polygon's ``composite_figi``. It survives ticker
renames (FB → META share the same composite_figi). For the small set of
tickers where Polygon does not return a composite_figi (some indices,
exotic types) we fall back to ``ticker`` as the identity key and flag
the row in the master via ``identity_source = 'ticker'``.

Change-log shape
~~~~~~~~~~~~~~~~
``change_type`` is one of:

- ``added`` — composite_figi appeared in the universe for the first time
- ``deactivated`` — was present (active) yesterday, absent (or inactive)
  today; we mark the master row's ``active = False``
- ``reactivated`` — was inactive in master, appears active in today's pull
- ``field_changed`` — a watched attribute changed value (``ticker``,
  ``name``, ``primary_exchange``, ``type``, etc.)
- ``ticker_renamed`` — emitted in addition to a ``field_changed`` row on
  ``ticker`` whenever ``get_ticker_events`` confirms an authoritative
  rename event with an ``event_date``

Why both ``field_changed(ticker, …)`` and ``ticker_renamed`` for the
same change? The first records what *we* observed (the diff between
yesterday and today). The second carries Polygon's authoritative
``event_date``, which may be earlier than our detection date. Keeping
both lets analysis answer either "when did we know" or "when did it
actually change."
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from massive import RESTClient
from massive.exceptions import BadResponse

from .config import DATA_DIR, REST_RATE_LIMIT
from .metrics import download_metrics
from .result import DownloadResult

logger = logging.getLogger(__name__)

CALL_INTERVAL = 60.0 / REST_RATE_LIMIT

# Universe scope ──────────────────────────────────────────────────────
# Pulled from Massive ``list_tickers`` per (market, type) combination.
# Indices ride along on ``market='indices'``; their ``type`` filter is
# not applied (Polygon's index types are heterogeneous).
TARGET_MARKETS: tuple[str, ...] = ("stocks", "fx", "indices")
EQUITY_TYPES: tuple[str, ...] = (
    "CS",      # common stock
    "ETF",     # exchange-traded fund
    "ADRC",    # American Depositary Receipt - Common
    "ADRP",    # ADR - Preferred
    "ETN",     # exchange-traded note
    "ETV",     # exchange-traded vehicle
    "FUND",    # closed-end fund
    "SP",      # structured product
    "RIGHT",   # rights offering
    "WARRANT", # warrant
)

# FX scope. Polygon returns the full cross-product of currency pairs
# (~3-4K tickers), but our store only needs USD-quoted pairs for
# foreign→USD conversion. Match the filter ``forex.py`` already uses.
FX_QUOTE_SUFFIX: str = "USD"

# Indices scope. Polygon's indices catalog has ~13K tickers, dominated
# by Nasdaq sub-sub-indices that aren't useful for our analytics. Restrict
# to a curated allowlist sourced from a CSV file (default
# ``data/indices_included.csv``) so the user can edit the included list
# without code changes. CSV must have a ``ticker`` column; other columns
# (name, type, etc.) are ignored.
INDEX_ALLOWLIST_FILENAME: str = "indices_included.csv"


def _load_index_allowlist(path: Path | None = None) -> frozenset[str]:
    """Read the index allowlist from CSV. Returns empty set on miss.

    Path resolution order:
        1. Explicit ``path`` argument (used in tests).
        2. ``<repo>/data/indices_included.csv`` (production).

    Missing file produces a warning and an empty set — indices are then
    omitted from the master entirely. That's the correct behavior: it's
    not a fatal error, and the diff against prior master will keep
    ignoring indices on every run until the file appears.
    """
    target = path or DATA_DIR / INDEX_ALLOWLIST_FILENAME
    if not target.exists():
        logger.warning(
            f"Index allowlist not found at {target}; no indices will be tracked"
        )
        return frozenset()
    df = pd.read_csv(target)
    if "ticker" not in df.columns:
        logger.warning(
            f"Index allowlist {target} has no 'ticker' column; ignoring"
        )
        return frozenset()
    tickers = df["ticker"].dropna().astype(str).str.upper().str.strip().tolist()
    return frozenset(t for t in tickers if t)

# Master row schema (column order is the on-disk schema).
MASTER_COLUMNS: tuple[str, ...] = (
    "composite_figi",
    "share_class_figi",
    "ticker",
    "name",
    "cik",
    "market",
    "type",
    "locale",
    "primary_exchange",
    "currency_name",
    "active",
    "polygon_last_updated_utc",
    "first_seen_at",
    "last_seen_at",
    "as_of",
    "identity_source",  # 'composite_figi' or 'ticker'
)

# Fields whose changes we record in the change log. ``polygon_last_updated_utc``
# is intentionally excluded — Polygon updates it whenever any internal
# field changes, so it would saturate the log with non-actionable rows.
WATCHED_FIELDS: tuple[str, ...] = (
    "ticker",
    "name",
    "cik",
    "type",
    "locale",
    "primary_exchange",
    "currency_name",
    "share_class_figi",
)

CHANGE_COLUMNS: tuple[str, ...] = (
    "change_id",
    "composite_figi",
    "ticker",          # ticker at time of change (denormalized for grep-ability)
    "change_type",
    "field_name",
    "old_value",
    "new_value",
    "event_date",
    "detected_at",
    "source",
    "run_id",
)


# ── Pulling current universe from the API ────────────────────────────


def _row_from_ticker(t, market: str) -> dict:
    """Map a Massive ticker object to a master-row dict."""
    composite_figi = getattr(t, "composite_figi", None)
    ticker = getattr(t, "ticker", None)
    return {
        "composite_figi": composite_figi,
        "share_class_figi": getattr(t, "share_class_figi", None),
        "ticker": ticker,
        "name": getattr(t, "name", None),
        "cik": getattr(t, "cik", None),
        "market": market,
        "type": getattr(t, "type", None),
        "locale": getattr(t, "locale", None),
        "primary_exchange": getattr(t, "primary_exchange", None),
        "currency_name": getattr(t, "currency_name", None),
        "active": bool(getattr(t, "active", True)),
        "polygon_last_updated_utc": getattr(t, "last_updated_utc", None),
        "identity_source": "composite_figi" if composite_figi else "ticker",
    }


def _identity_key(row: dict | pd.Series) -> str | None:
    """Return composite_figi if present, else fall back to 'TICKER:<ticker>'."""
    figi = row.get("composite_figi") if isinstance(row, dict) else row["composite_figi"]
    if figi:
        return figi
    ticker = row.get("ticker") if isinstance(row, dict) else row["ticker"]
    return f"TICKER:{ticker}" if ticker else None


class PartialUniverseError(RuntimeError):
    """Raised when one or more (market, type) pulls fail in strict mode.

    Rationale: a transient API failure on one segment would otherwise
    leave that segment's tickers absent from today's universe, which
    the diff layer would interpret as deactivation — emitting potentially
    thousands of false ``deactivated`` rows. Failing fast preserves the
    integrity of the change log.
    """


def fetch_universe(
    client: RESTClient,
    *,
    index_allowlist_path: Path | None = None,
    strict: bool = True,
) -> pd.DataFrame:
    """Pull current active universe across configured markets/types.

    Issues one ``list_tickers`` call per (market, type) combination for
    equity types, plus one combined call per non-stocks market.
    Indices are filtered to the allowlist defined in
    ``data/indices_included.csv`` (or override via
    ``index_allowlist_path``).

    Args:
        strict: When True (default), any failed (market, type) pull
            raises :class:`PartialUniverseError` after the loop. When
            False, failures are logged as warnings and the partial
            result is returned — diff layer will then produce
            ``deactivated`` rows for the missing segments. Use only
            when you've separately verified that the diff impact is
            acceptable.
    """
    index_allowlist = _load_index_allowlist(index_allowlist_path)
    rows: list[dict] = []
    failures: list[str] = []

    for market in TARGET_MARKETS:
        if market == "stocks":
            for ttype in EQUITY_TYPES:
                logger.info(f"Fetching universe: market={market} type={ttype}")
                try:
                    tickers = list(
                        client.list_tickers(
                            market=market, type=ttype, active=True, limit=1000
                        )
                    )
                except Exception as e:  # noqa: BLE001 - record + continue or raise after loop
                    logger.warning(f"  list_tickers failed for {market}/{ttype}: {e}")
                    failures.append(f"{market}/{ttype}: {e}")
                    continue
                rows.extend(_row_from_ticker(t, market) for t in tickers)
                time.sleep(CALL_INTERVAL)
        else:
            logger.info(f"Fetching universe: market={market}")
            try:
                tickers = list(client.list_tickers(market=market, active=True, limit=1000))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"  list_tickers failed for {market}: {e}")
                failures.append(f"{market}: {e}")
                continue
            if market == "fx":
                # Restrict to USD-quoted pairs (e.g. ``C:EURUSD``). Without
                # this filter Polygon returns ~3-4K cross-pairs we don't store.
                before = len(tickers)
                tickers = [
                    t for t in tickers
                    if (getattr(t, "ticker", "") or "").endswith(FX_QUOTE_SUFFIX)
                ]
                logger.info(
                    f"  fx: filtered {before} → {len(tickers)} USD-quoted pairs"
                )
            elif market == "indices":
                # Restrict to curated allowlist sourced from CSV. Polygon's
                # catalog has ~13K indices dominated by sub-sub-indices we
                # don't use.
                before = len(tickers)
                tickers = [
                    t for t in tickers
                    if (getattr(t, "ticker", "") or "").upper() in index_allowlist
                ]
                logger.info(
                    f"  indices: filtered {before} → {len(tickers)} "
                    f"(allowlist size: {len(index_allowlist)})"
                )
            rows.extend(_row_from_ticker(t, market) for t in tickers)
            time.sleep(CALL_INTERVAL)

    if failures and strict:
        raise PartialUniverseError(
            f"Universe pull had {len(failures)} failed segment(s); refusing "
            f"to write a partial master. Failures: {failures}"
        )

    df = pd.DataFrame(rows, columns=[c for c in MASTER_COLUMNS if c not in
                                      ("first_seen_at", "last_seen_at", "as_of")])
    if not df.empty:
        # Drop true duplicates (same identity appearing in multiple type pulls
        # — rare, but possible if Polygon ever cross-classifies).
        df = df.drop_duplicates(subset=["composite_figi", "ticker"], keep="first")
    logger.info(
        f"Universe fetched: {len(df)} rows "
        f"({df['market'].value_counts().to_dict() if not df.empty else {}})"
    )
    return df


# ── Pure diff against prior master ───────────────────────────────────


def _identity_index(df: pd.DataFrame) -> pd.Series:
    """Return a Series of identity keys aligned to df.index."""
    figi = df["composite_figi"].fillna("")
    fallback = "TICKER:" + df["ticker"].fillna("")
    return figi.where(figi != "", fallback)


def _stringify(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)


def _normalize_event_date(value) -> str | None:
    """Normalize an event_date to ``YYYY-MM-DD`` regardless of input shape.

    Cache-derived events come from ``valid_from`` (a Timestamp). API
    events come as a string from Polygon (often ISO with time component).
    Without a single canonical format, the dedupe key
    ``(composite_figi, event_date, new_value)`` would treat
    ``"2012-05-18"`` and ``"2012-05-18T00:00:00Z"`` as different events
    and emit duplicate rows.
    """
    if value is None:
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def compute_changes(
    prev: pd.DataFrame,
    new: pd.DataFrame,
    *,
    run_id: str,
    detected_at: datetime,
) -> pd.DataFrame:
    """Diff two universe frames and produce change-log rows.

    Both frames must contain (at minimum) the columns in ``MASTER_COLUMNS``
    that participate in identity + WATCHED_FIELDS + ``active``. ``prev``
    may be empty (bootstrap case → every new row becomes ``added``).
    """
    if new.empty:
        return pd.DataFrame(columns=list(CHANGE_COLUMNS))

    new_idx = _identity_index(new)
    new_by_id = new.assign(_id=new_idx.values).set_index("_id", drop=True)

    if prev.empty:
        added_rows = [
            {
                "change_id": str(uuid.uuid4()),
                "composite_figi": row.get("composite_figi"),
                "ticker": row.get("ticker"),
                "change_type": "added",
                "field_name": None,
                "old_value": None,
                "new_value": None,
                "event_date": None,
                "detected_at": detected_at,
                "source": "bootstrap",
                "run_id": run_id,
            }
            for _, row in new_by_id.iterrows()
        ]
        return pd.DataFrame(added_rows, columns=list(CHANGE_COLUMNS))

    prev_idx = _identity_index(prev)
    prev_by_id = prev.assign(_id=prev_idx.values).set_index("_id", drop=True)

    added_ids = new_by_id.index.difference(prev_by_id.index)
    removed_ids = prev_by_id.index.difference(new_by_id.index)
    common_ids = new_by_id.index.intersection(prev_by_id.index)

    rows: list[dict] = []

    # ── Additions / reactivations ──
    for ident in added_ids:
        n = new_by_id.loc[ident]
        rows.append({
            "change_id": str(uuid.uuid4()),
            "composite_figi": n.get("composite_figi"),
            "ticker": n.get("ticker"),
            "change_type": "added",
            "field_name": None,
            "old_value": None,
            "new_value": None,
            "event_date": None,
            "detected_at": detected_at,
            "source": "list_tickers",
            "run_id": run_id,
        })

    # ── Deactivations ──
    # Anything in prev that's missing from today's *active* pull and was
    # marked active in prev is now deactivated. (Inactive rows that stay
    # absent generate no change.)
    for ident in removed_ids:
        p = prev_by_id.loc[ident]
        if not bool(p.get("active", False)):
            continue
        rows.append({
            "change_id": str(uuid.uuid4()),
            "composite_figi": p.get("composite_figi"),
            "ticker": p.get("ticker"),
            "change_type": "deactivated",
            "field_name": "active",
            "old_value": "True",
            "new_value": "False",
            "event_date": None,
            "detected_at": detected_at,
            "source": "list_tickers",
            "run_id": run_id,
        })

    # ── Reactivations + field changes on common identities ──
    for ident in common_ids:
        p = prev_by_id.loc[ident]
        n = new_by_id.loc[ident]

        # Reactivation: prev.active = False, today active = True
        if not bool(p.get("active", False)) and bool(n.get("active", False)):
            rows.append({
                "change_id": str(uuid.uuid4()),
                "composite_figi": n.get("composite_figi"),
                "ticker": n.get("ticker"),
                "change_type": "reactivated",
                "field_name": "active",
                "old_value": "False",
                "new_value": "True",
                "event_date": None,
                "detected_at": detected_at,
                "source": "list_tickers",
                "run_id": run_id,
            })

        for field in WATCHED_FIELDS:
            old_v = p.get(field) if field in p.index else None
            new_v = n.get(field) if field in n.index else None
            old_s = _stringify(old_v)
            new_s = _stringify(new_v)
            if old_s != new_s:
                rows.append({
                    "change_id": str(uuid.uuid4()),
                    "composite_figi": n.get("composite_figi"),
                    "ticker": n.get("ticker"),
                    "change_type": "field_changed",
                    "field_name": field,
                    "old_value": old_s,
                    "new_value": new_s,
                    "event_date": None,
                    "detected_at": detected_at,
                    "source": "list_tickers",
                    "run_id": run_id,
                })

    return pd.DataFrame(rows, columns=list(CHANGE_COLUMNS))


# ── Ticker Events backfill (authoritative rename history) ────────────

SOURCE_CACHE = "ticker_events_cache"
SOURCE_API = "ticker_events_api"


class _EventCache:
    """Pre-computed lookup over ``ticker_events.parquet`` for O(1) access.

    Built once at the start of a sync and used per-ticker in the inner
    loop. Replaces the old per-call DataFrame scan, which was
    ``O(rows × tickers)`` — ~132M comparisons for a typical sync.

    Lookup semantics match the previous DataFrame-based code:

    - ``ticker in cache`` is True when the parquet has any row for that
      ticker. Used to distinguish "stub: no renames" from "not yet checked."
    - ``cache.events_for(ticker)`` returns the rename chain for the
      preferred figi (``is_current=True`` wins on collisions like "META"
      having been used by multiple securities), normalized to the same
      shape as the API response.
    """

    def __init__(self, events_df: pd.DataFrame | None):
        self._known: frozenset[str] = frozenset()
        self._ticker_to_figi: dict[str, object] = {}
        self._chains: dict[object, list[tuple[str, str]]] = {}

        if events_df is None or events_df.empty:
            return

        df = events_df.copy()
        df["_t"] = df["ticker"].astype(str).str.upper()
        self._known = frozenset(df["_t"])

        # ticker → figi, preferring is_current=True for reused tickers.
        if "is_current" in df.columns:
            sort_df = df.assign(
                _is_current=df["is_current"].fillna(False).astype(bool)
            ).sort_values("_is_current", ascending=False, kind="mergesort")
        else:
            sort_df = df
        self._ticker_to_figi = (
            sort_df.drop_duplicates("_t", keep="first")
                   .set_index("_t")["composite_figi"]
                   .to_dict()
        )

        # figi → list of (ticker, normalized_event_date) for non-stub rows.
        figi_groups = df.dropna(subset=["composite_figi"]).groupby("composite_figi")
        for figi, group in figi_groups:
            chain: list[tuple[str, str]] = []
            for tk, vf in zip(
                group["ticker"].tolist(),
                group["valid_from"].tolist(),
                strict=False,
            ):
                date_str = _normalize_event_date(vf)
                if not date_str:
                    continue
                chain.append((tk, date_str))
            self._chains[figi] = chain

    def __contains__(self, ticker: str) -> bool:
        return (ticker or "").upper() in self._known

    def events_for(self, ticker: str) -> list[dict] | None:
        """Return cached events list or ``None`` when ticker is not in cache.

        An empty list (``[]``) means "in cache, but no rename history" —
        a valid cache hit that should suppress the API call.
        """
        t = (ticker or "").upper()
        if t not in self._known:
            return None
        figi = self._ticker_to_figi.get(t)
        if figi is None or (isinstance(figi, float) and pd.isna(figi)):
            return []
        chain = self._chains.get(figi, [])
        return [
            {
                "type": "ticker_change",
                "date": event_date,
                "ticker_change": {"ticker": tk, "composite_figi": figi},
            }
            for tk, event_date in chain
        ]


def _events_for_ticker(
    client: RESTClient,
    ticker: str,
    *,
    cache: _EventCache | None = None,
) -> tuple[list[dict], str]:
    """Pull ticker_change events. Returns ``(events, source)``.

    Cache short-circuits the API for any ticker known to the cache,
    including stub entries (returns ``[]``). API errors that look like
    auth/notfound are silently swallowed; other errors are warned.
    """
    if cache is not None:
        cached = cache.events_for(ticker)
        if cached is not None:
            return cached, SOURCE_CACHE
    try:
        result = client.get_ticker_events(ticker)
    except BadResponse as e:
        msg = str(e)
        if "NOT_AUTHORIZED" not in msg and "NOT_FOUND" not in msg:
            logger.warning(f"  get_ticker_events({ticker}) error: {e}")
        return [], SOURCE_API
    except Exception as e:  # noqa: BLE001 - surface as warning, don't kill sync
        logger.warning(f"  get_ticker_events({ticker}) unexpected error: {e}")
        return [], SOURCE_API
    return [e for e in (result.events or []) if e.get("type") == "ticker_change"], SOURCE_API


def backfill_rename_history(
    client: RESTClient,
    tickers: Iterable[str],
    *,
    run_id: str,
    detected_at: datetime,
    cache: pd.DataFrame | _EventCache | None = None,
) -> pd.DataFrame:
    """For each ticker, pull events (cache-first) and emit ``ticker_renamed`` rows.

    Returns a DataFrame of change-log rows (one per historical rename).
    Rate-limited at the module's ``CALL_INTERVAL`` between **API** calls;
    cache hits don't sleep. ``cache`` can be a raw events DataFrame
    (built into a lookup once) or a pre-built :class:`_EventCache`.
    """
    if isinstance(cache, pd.DataFrame) or cache is None:
        cache = _EventCache(cache if isinstance(cache, pd.DataFrame) else None)
    rows: list[dict] = []
    tickers = sorted({t for t in tickers if t})
    last_was_api = False
    for ticker in tickers:
        if last_was_api:
            time.sleep(CALL_INTERVAL)
        events, source = _events_for_ticker(client, ticker, cache=cache)
        last_was_api = source == SOURCE_API
        for ev in events:
            tc = ev.get("ticker_change", {}) or {}
            new_ticker = tc.get("ticker")
            event_date = _normalize_event_date(ev.get("date"))
            if not new_ticker or not event_date:
                continue
            rows.append({
                "change_id": str(uuid.uuid4()),
                "composite_figi": getattr(ev, "composite_figi", None) or tc.get("composite_figi"),
                "ticker": new_ticker,
                "change_type": "ticker_renamed",
                "field_name": "ticker",
                "old_value": None,  # event API returns chain, not pairs
                "new_value": new_ticker,
                "event_date": event_date,
                "detected_at": detected_at,
                "source": source,
                "run_id": run_id,
            })
    return pd.DataFrame(rows, columns=list(CHANGE_COLUMNS))


def _dedupe_rename_rows(
    new_changes: pd.DataFrame, existing_changes: pd.DataFrame
) -> pd.DataFrame:
    """Drop ``ticker_renamed`` rows that duplicate ones already seen.

    Identity for renames: ``(composite_figi, event_date, new_value)``.
    Dedupe runs in two passes:

    1. *Within* ``new_changes`` — when two tickers (e.g. ``FB`` and
       ``META``) share a composite_figi, looking up either returns the
       same chain, so the same event would be emitted twice. We keep
       the first occurrence.
    2. *Against* ``existing_changes`` — re-running a sync (especially
       ``--full-event-backfill``) must not re-append historical events
       already in the log.

    Non-rename rows pass through untouched (their ``detected_at`` +
    ``run_id`` keep them unique by construction).
    """
    if new_changes.empty:
        return new_changes
    rename_mask = new_changes["change_type"] == "ticker_renamed"
    if not rename_mask.any():
        return new_changes

    rename_key_cols = ["composite_figi", "event_date", "new_value"]

    def _keys(df: pd.DataFrame) -> list[tuple[str, str, str]]:
        # Tuple-of-strings per row. NaT/NaN values cast to "NaT"/"nan",
        # which compare equal across frames — that's what we want for
        # a stable identity key.
        return [
            (str(a), str(b), str(c))
            for a, b, c in zip(
                df["composite_figi"].tolist(),
                df["event_date"].tolist(),
                df["new_value"].tolist(),
                strict=False,
            )
        ]

    rename_rows = new_changes.loc[rename_mask].copy()

    # Pass 1: within-run dedupe
    rename_rows = rename_rows.drop_duplicates(
        subset=rename_key_cols, keep="first"
    )

    # Pass 2: against existing log
    if existing_changes is not None and not existing_changes.empty:
        existing_renames = existing_changes[
            existing_changes["change_type"] == "ticker_renamed"
        ]
        if not existing_renames.empty:
            seen = set(_keys(existing_renames))
            keys = _keys(rename_rows)
            mask = [k not in seen for k in keys]
            rename_rows = rename_rows.loc[
                rename_rows.index[mask] if rename_rows.index.size else []
            ]

    non_rename = new_changes.loc[~rename_mask]
    return pd.concat([non_rename, rename_rows], ignore_index=True)


# ── Master row maintenance (carry first_seen_at / last_seen_at) ──────


def merge_master(
    prev: pd.DataFrame,
    new: pd.DataFrame,
    *,
    detected_at: datetime,
) -> pd.DataFrame:
    """Produce the new master frame from prior + today's pull.

    Carries ``first_seen_at`` from prior rows; sets ``last_seen_at`` and
    ``as_of`` on every row touched today; preserves prior rows that are
    absent from today's pull but flips their ``active`` to False.
    """
    if new.empty and prev.empty:
        return pd.DataFrame(columns=list(MASTER_COLUMNS))

    new_idx = _identity_index(new)
    new = new.assign(_id=new_idx.values)

    if prev.empty:
        new["first_seen_at"] = detected_at
        new["last_seen_at"] = detected_at
        new["as_of"] = detected_at
        return new.drop(columns="_id")[list(MASTER_COLUMNS)]

    prev_idx = _identity_index(prev)
    prev = prev.assign(_id=prev_idx.values)

    new_by_id = new.set_index("_id", drop=False)
    prev_by_id = prev.set_index("_id", drop=False)

    # Rows present in today's pull → take new values, carry first_seen_at
    seen_ids = new_by_id.index
    seen = new_by_id.copy()
    carried = prev_by_id["first_seen_at"].reindex(seen_ids)
    seen["first_seen_at"] = carried.fillna(detected_at)
    seen["last_seen_at"] = detected_at
    seen["as_of"] = detected_at

    # Rows in prior master but not in today's pull → keep, but mark inactive
    missing_ids = prev_by_id.index.difference(seen_ids)
    missing = prev_by_id.loc[missing_ids].copy()
    if not missing.empty:
        missing["active"] = False
        missing["as_of"] = detected_at
        # last_seen_at unchanged (we did NOT see it today)

    merged = pd.concat([seen, missing], ignore_index=False)
    merged = merged.drop(columns="_id", errors="ignore")
    # Ensure all expected columns are present and ordered.
    for col in MASTER_COLUMNS:
        if col not in merged.columns:
            merged[col] = None
    return merged[list(MASTER_COLUMNS)].reset_index(drop=True)


# ── Top-level orchestrator ───────────────────────────────────────────


def _read_parquet_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write a DataFrame to parquet atomically.

    Writes to ``<path>.tmp`` first, then ``os.replace`` swaps it into
    place. ``os.replace`` is atomic on POSIX (and on Windows since
    Python 3.3), so readers always see either the old file or the new
    file, never a half-written one. If the process is killed during the
    write, the original ``path`` is preserved untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False, engine="pyarrow")
    os.replace(tmp, path)


def _append_changes(path: Path, new_changes: pd.DataFrame) -> int:
    """Append change rows, rewriting the file atomically. Returns total row count."""
    existing = _read_parquet_or_empty(path)
    if new_changes.empty:
        return len(existing)
    out = new_changes if existing.empty else pd.concat(
        [existing, new_changes], ignore_index=True
    )
    _atomic_write_parquet(out, path)
    return len(out)


def sync_security_master(
    output_dir: Path | None = None,
    api_key: str | None = None,
    *,
    backfill_events: bool = True,
    full_event_backfill: bool = False,
    write_dated_snapshot: bool = True,
    index_allowlist_path: Path | None = None,
    strict_universe: bool = True,
) -> DownloadResult:
    """Pull universe, diff against prior master, persist master + changes.

    Args:
        output_dir: Directory to write parquet files.
            Defaults to ``data/reference/``.
        api_key: Massive/Polygon API key. Falls back to ``MASSIVE_API_KEY``.
        backfill_events: If True, on additions and *any* detected field
            change, also pull ``get_ticker_events`` and emit authoritative
            ``ticker_renamed`` change rows. Cache from
            ``ticker_events.parquet`` is consulted first to avoid
            redundant API calls.
        full_event_backfill: If True, after the diff phase, also pull
            ``get_ticker_events`` for *every* identity in the new master.
            Use as a one-shot to seed history for securities that existed
            before the first sync. Idempotent: ``ticker_renamed`` rows
            already in the change log are dropped before append.
        write_dated_snapshot: If True, also write a dated raw-pull snapshot
            to ``data/reference/snapshots/tickers_<YYYY-MM-DD>.parquet``.

    Returns:
        :class:`DownloadResult` with row/byte counts and timing.
    """
    output_dir = output_dir or DATA_DIR / "reference"
    output_dir.mkdir(parents=True, exist_ok=True)

    key = api_key or os.getenv("MASSIVE_API_KEY")
    client = RESTClient(api_key=key)

    master_path = output_dir / "security_master.parquet"
    changes_path = output_dir / "security_master_changes.parquet"

    run_id = str(uuid.uuid4())
    detected_at = datetime.now(UTC)

    with download_metrics("sync_security_master", output_dir=output_dir) as m:
        # 1) Fetch current universe
        universe = fetch_universe(
            client,
            index_allowlist_path=index_allowlist_path,
            strict=strict_universe,
        )
        m.requested = len(universe)

        if universe.empty:
            m.warnings.append("empty universe pull — nothing written")
            return m.result

        # 2) Diff against prior master (if any)
        prev_master = _read_parquet_or_empty(master_path)
        list_changes = compute_changes(
            prev_master, universe, run_id=run_id, detected_at=detected_at
        )
        logger.info(
            f"Diff: {len(list_changes)} list-derived changes "
            f"({list_changes['change_type'].value_counts().to_dict() if not list_changes.empty else {}})"
        )

        # 3) Backfill rename history. Triggers:
        #    - any `added` identity
        #    - any `field_changed` row (defensive: a rename can manifest
        #      as a flip on `ticker`, `cik`, or `name` depending on how
        #      Polygon represents it for that security)
        #    - if full_event_backfill, every identity in the new master
        # Restricted to ``market == 'stocks'`` — Polygon's events endpoint
        # is stocks-only and returns 404 for fx pairs and indices. Without
        # this filter a bootstrap with ~13K indices wastes hours on 404s.
        events_changes = pd.DataFrame(columns=list(CHANGE_COLUMNS))
        events_cache = _read_parquet_or_empty(output_dir / "ticker_events.parquet")
        if events_cache.empty:
            events_cache = None  # treat missing file the same as no entries

        events_eligible_tickers: set[str] = set(
            t for t in universe.loc[universe["market"] == "stocks", "ticker"]
                              .dropna().tolist()
            if t
        )

        tickers_to_check: set[str] = set()
        if backfill_events and not list_changes.empty:
            triggered_mask = (
                (list_changes["change_type"] == "added")
                | (list_changes["change_type"] == "field_changed")
            )
            tickers_to_check.update(
                list_changes.loc[triggered_mask, "ticker"].dropna().tolist()
            )

        if full_event_backfill:
            tickers_to_check.update(events_eligible_tickers)

        # Drop non-stocks tickers (events API doesn't support them)
        tickers_to_check &= events_eligible_tickers

        if tickers_to_check:
            cache_size = 0 if events_cache is None else len(events_cache)
            logger.info(
                f"Backfilling ticker events for {len(tickers_to_check)} tickers "
                f"(cache: {cache_size} rows)"
            )
            events_changes = backfill_rename_history(
                client,
                tickers_to_check,
                run_id=run_id,
                detected_at=detected_at,
                cache=events_cache,
            )

        all_changes = (
            pd.concat([list_changes, events_changes], ignore_index=True)
            if not events_changes.empty
            else list_changes
        )

        # Dedupe ticker_renamed rows against existing log so re-running
        # full_event_backfill (or repeated incremental backfills) doesn't
        # accumulate duplicate rows for the same historical event.
        existing_changes = _read_parquet_or_empty(changes_path)
        all_changes = _dedupe_rename_rows(all_changes, existing_changes)

        # 4) Build the new master (atomic write)
        new_master = merge_master(prev_master, universe, detected_at=detected_at)
        _atomic_write_parquet(new_master, master_path)
        m.rows_written += len(new_master)
        m.files_written += 1
        m.bytes_written += master_path.stat().st_size

        # 5) Append change log (atomic rewrite of full file)
        total_changes = _append_changes(changes_path, all_changes)
        if not all_changes.empty:
            m.rows_written += len(all_changes)
            m.files_written += 1
            m.bytes_written += changes_path.stat().st_size

        # 6) Optional dated raw snapshot — best-effort; don't fail the run
        if write_dated_snapshot:
            snap_path = (
                output_dir / "snapshots"
                / f"tickers_{detected_at.date().isoformat()}.parquet"
            )
            try:
                _atomic_write_parquet(universe, snap_path)
                m.files_written += 1
                m.bytes_written += snap_path.stat().st_size
            except Exception as e:  # noqa: BLE001 - snapshot is non-critical
                logger.warning(f"snapshot write failed ({snap_path}): {e}")
                m.warnings.append(f"snapshot write failed: {e}")

        m.completed = len(new_master)
        logger.info(
            f"Security master synced: {len(new_master)} master rows, "
            f"{len(all_changes)} new change rows ({total_changes} total in log)"
        )

    return m.result
