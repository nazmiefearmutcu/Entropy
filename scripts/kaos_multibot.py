#!/usr/bin/env python3
"""KAOS MULTIBOT supervisor — ONE bot that scans crypto AND NASDAQ simultaneously.

Modes (same script, chosen by argv):

  (parent, default)  Supervises one child process per enabled venue. A dead
      child is restarted with exponential backoff; a crash-loop (child dying
      younger than CRASH_STABLE_S) escalates the backoff up to
      RESTART_BACKOFF_MAX_S. A per-venue pause file
      (``<ops-dir>/kaos-multibot.pause.<venue>``, ops-style: file exists =
      venue paused, delete file = resume) suppresses spawning. The parent
      writes the AGGREGATE state file ``reports/live-paper/state-multibot.json``
      atomically for the panels. The parent NEVER places orders, NEVER
      flattens, NEVER deletes state — it only spawns/observes/restarts children.

  --venue crypto     The CRYPTO child. Byte-identical signal lane to the
      standalone live runner (``scripts/entropy_live_paper.py``): same
      DEFAULT_SYMBOLS (20 majors), same build_cfg, same env contract
      (KAOS_EXCHANGE_* — LEVERAGE/SIZING_PCT/SLOTS/etc. flow straight through),
      same executor selection (TestnetExecutor when keys are present,
      paper-only otherwise), same feed-bar spacing. The ONLY deltas are (a)
      the state file is ``state-crypto.json`` and (b) the env carries
      KAOS_VENUE=crypto so watchdog lanes can discriminate. On first boot, if
      ``state-crypto.json`` does not exist yet, the child seeds its restart
      accounting (equity base + mirror provenance + risk breaker) from the
      LIVE ``state.json`` READ-ONLY (the live file is never written) so the
      migration from the standalone runner is accounting-continuous.

  --venue nasdaq     The NASDAQ child. LivePaper-STYLE loop built directly on
      the PURE layers (entropy.bot.runner.BotRunner + ConsensusStrategy +
      RiskManager) with the AlpacaEquitiesAdapter venue. This is the sanctioned
      fallback (the LivePaper constructor hard-codes the crypto harness
      globals: binance-spot symbol resolver, Binance klines fetcher and
      make_executor_from_env — injecting an equity venue without edits is not
      possible). USEquitiesClock gates everything: outside RTH the child is
      IDLE (no bar polling; a 60 s heartbeat still refreshes its state file so
      the watchdog sees liveness); at open it backfills 300 15m bars then
      polls like the crypto lane. Executor = AlpacaEquitiesAdapter when Alpaca
      keys are present (connect_test() runs first; on failure the lane stays
      paper-only with an honest banner), else paper-only. PdtCounter is wired
      into the venue state dir. Funding is None (M2 gate no-ops). Risk profile
      = the crypto knobs verbatim (20σ/4σ barriers, daily-loss 15%,
      max_concurrent 4, exposure 40%) with leverage forced 1.0 by the adapter.

Env:
  KAOS_VENUE           discriminator set by the parent for every child (also
                       forced by the child itself when run manually).
  KAOS_MULTIBOT_VENUES parent override, e.g. "crypto" or "crypto,nasdaq".
  KAOS_MULTIBOT_OPS_DIR parent pause-file directory (default C:\\botmonitor\\ops).
  KAOS_MULTIBOT_EQUITY_CASH nasdaq starting cash (default 25000).
  KAOS_ALPACA_*        adapter knobs (see entropy.venues.alpaca_equities).

Deployment (owner decision, NOT auto-wired): C:\\botmonitor\\ops\\run-kaos-multibot.cmd
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
for _p in ((REPO / "src"), (REPO / "scripts")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from entropy.venues import (  # noqa: E402
    AlpacaEquitiesAdapter,
    PdtCounter,
    USEquitiesClock,
)

NS = 1_000_000_000             # seconds -> ns
MS_NS = 1_000_000              # milliseconds -> ns (LivePaper's convention)
BAR_MS = 15 * 60 * 1000

# ---- paths -----------------------------------------------------------------
OUT_DIR = REPO / "reports" / "live-paper"
CRYPTO_STATE_NAME = "state-crypto.json"
NASDAQ_STATE_NAME = "state-nasdaq.json"
AGG_STATE_NAME = "state-multibot.json"
LIVE_STATE_NAME = "state.json"          # the standalone runner's file: READ-ONLY
OPS_DIR_DEFAULT = Path(r"C:\botmonitor\ops")
PAUSE_PREFIX = "kaos-multibot.pause"    # ops-style: <prefix>.<venue> = paused

# ---- parent ----------------------------------------------------------------
VENUES = ("crypto", "nasdaq")
SUPERVISOR_POLL_S = 5.0
RESTART_BACKOFF_BASE_S = 2.0            # 2 -> 4 -> 8 ... capped:
RESTART_BACKOFF_MAX_S = 600.0
CRASH_STABLE_S = 300.0                  # child younger than this = a crash
_TERMINATE_WAIT_S = 10.0
SUPERVISOR_LOCK_NAME = "kaos-multibot.lock"   # H-1b: single-instance guard
STANDALONE_FRESH_S = 20 * 60            # live state.json younger than this
                                        # = standalone runner likely WRITING it

# ---- nasdaq child ----------------------------------------------------------
EQUITY_POLL_S = 15.0
IDLE_HEARTBEAT_S = 60.0                 # state heartbeat while IDLE (watchdog)
EQUITY_BACKFILL_BARS = 300              # 100 warmup + 200 evaluated (crypto parity)
EQUITY_CASH_DEFAULT = 25000.0
NASDAQ_UNIVERSE_N = 20
EQUITY_FETCH_MAX_ATTEMPTS = 3
EQUITY_FETCH_BACKOFF_START_S = 1.0
EQUITY_FETCH_BACKOFF_MAX_S = 60.0
EQUITY_HISTORY_MAX = 500
EQUITY_CLOSED_MAX = 40
EQUITY_RECONCILE_S = 900.0              # venue bracket re-sync cadence (crypto parity)

NASDAQ_BANNER_NOKEYS = "PAPER (no Alpaca keys)"   # frozen honesty string


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(x: Any) -> float:
    """Sanitize a float for JSON state (no NaN/Inf — same contract as the
    live runner's state writer)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def write_atomic(path: Path, obj: Any) -> None:
    """Atomic tmp+fsync+replace JSON write (state-writer discipline shared
    with the live runner). A failed write keeps the previous file intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        data = json.dumps(obj, allow_nan=False)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


# ============================================================================
# Parent: pure helpers (unit-tested)
# ============================================================================

def build_child_env(base_env: Any, venue: str) -> dict[str, str]:
    """Child env = the parent's env VERBATIM (KAOS_EXCHANGE_* /
    KAOS_ALPACA_* / APCA_* all flow through) + the KAOS_VENUE discriminator.
    Nothing is removed — the crypto child must see exactly what the
    standalone runner would see."""
    env = {str(k): str(v) for k, v in dict(base_env).items()}
    env["KAOS_VENUE"] = venue
    return env


def next_restart_delay_s(crashes: int) -> float:
    """Exponential restart backoff with crash-loop escalation:
    2, 4, 8, ... capped at RESTART_BACKOFF_MAX_S."""
    return min(RESTART_BACKOFF_BASE_S * (2 ** max(0, min(int(crashes), 16))),
               RESTART_BACKOFF_MAX_S)


def update_crash_streak(crashes: int, lifetime_s: float) -> int:
    """A child that died younger than CRASH_STABLE_S escalates the streak;
    a child that ran stably resets it."""
    return 0 if float(lifetime_s) >= CRASH_STABLE_S else int(crashes) + 1


def pause_file_path(ops_dir: Path | str, venue: str) -> Path:
    return Path(ops_dir) / f"{PAUSE_PREFIX}.{venue}"


def is_paused(ops_dir: Path | str, venue: str) -> bool:
    return pause_file_path(ops_dir, venue).exists()


# ---- H-1b: single-instance lock + standalone-runner collision gate ---------
def pid_alive(pid: int) -> bool:
    """Windows-safe pid liveness probe. NEVER os.kill(pid, 0) on Windows:
    CPython implements it via TerminateProcess (exit code 0) — the probe
    itself would kill the process. ctypes OpenProcess + exit-code check;
    falls back to False on any failure (conservative: refuses to start)."""
    if pid <= 0:
        return False
    try:
        import ctypes  # noqa: PLC0415 — stdlib, probe-only
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 — non-Windows or probe failure
        return False


def supervisor_lock_path(out_dir: Path | str) -> Path:
    return Path(out_dir) / SUPERVISOR_LOCK_NAME


def acquire_supervisor_lock(out_dir: Path | str,
                            now_s: float | None = None) -> tuple[bool, str]:
    """Atomic create-if-absent lock (O_EXCL) carrying our pid. Refuses when
    the lock exists AND its pid is alive; a STALE lock (dead pid) is taken
    over. (pid_alive is conservative-False on probe failure => refuses.)"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = supervisor_lock_path(out_dir)
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        old_pid = int(data.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        old_pid = 0
    if old_pid and pid_alive(old_pid):
        return False, (f"another supervisor pid={old_pid} holds "
                       f"{lock.name} — refusing to double-start")
    payload = {"pid": os.getpid(), "ts_utc": _utc_now_iso(),
               "venues": list(VENUES)}
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except FileExistsError:
        return False, f"{lock.name} appeared concurrently — refusing"
    except OSError as exc:
        return False, f"lock write failed ({exc}) — refusing"
    return True, f"lock acquired pid={os.getpid()}"


def release_supervisor_lock(out_dir: Path | str) -> None:
    """Remove OUR lock only (pid match); a takeover lock is left alone."""
    lock = supervisor_lock_path(out_dir)
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        if int(data.get("pid") or 0) == os.getpid():
            lock.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def standalone_runner_active(out_dir: Path | str,
                             fresh_s: float = STANDALONE_FRESH_S,
                             now_s: float | None = None) -> tuple[bool, str]:
    """H-1b collision gate: the standalone runner is 'active' when its
    READ-ONLY live state.json was heartbeat-refreshed within ``fresh_s``.
    Freshness is the same liveness evidence the watchdog uses; a hung
    writer is the watchdog's escalation problem, not ours."""
    now_s = time.time() if now_s is None else float(now_s)
    path = Path(out_dir) / LIVE_STATE_NAME
    try:
        age = now_s - path.stat().st_mtime
    except OSError:
        return False, "no live state.json — standalone runner not running"
    if age <= float(fresh_s):
        return True, (f"live state.json refreshed {age:.0f}s ago "
                      f"(< {fresh_s:.0f}s) — standalone runner ACTIVE")
    return False, (f"live state.json stale ({age:.0f}s) — "
                   "standalone runner considered DOWN")


def read_child_summary(state_path: Path | str,
                       now_s: float | None = None) -> dict[str, Any] | None:
    """Defensively read a child's state file for the aggregate writer.
    Returns {equity, open_positions, mode, state_age_s, session} or None.
    Never raises (a corrupt/missing child file must not kill the parent)."""
    now_s = time.time() if now_s is None else float(now_s)
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    age: float | None = None
    try:
        age = max(0.0, now_s - datetime.fromisoformat(
            str(data.get("ts_utc"))).timestamp())
    except (ValueError, TypeError, OverflowError):
        try:
            age = max(0.0, now_s - Path(state_path).stat().st_mtime)
        except OSError:
            pass
    positions = data.get("positions")
    n_open = len(positions) if isinstance(positions, (list, tuple)) else None
    equity = data.get("equity_usd")
    return {
        "equity": _f(equity) if equity is not None else None,
        "open_positions": n_open,
        "mode": str(data.get("mode") or "unknown"),
        "state_age_s": age,
        "session": data.get("session"),
    }


def build_aggregate(venue_entries: dict[str, dict[str, Any]],
                    supervisor_pid: int,
                    ts_utc: str | None = None) -> dict[str, Any]:
    """Aggregate state schema (panels + watchdog read this):
    {ts_utc, supervisor_pid, venues:{<vid>:{pid, running, state_age_s, equity,
    open_positions, mode, paused, crashes, restarts, exit_code,
    backoff_remaining_s}}}. Additive keys only — consumer-safe."""
    return {
        "ts_utc": ts_utc or _utc_now_iso(),
        "supervisor_pid": int(supervisor_pid),
        "venues": {vid: {
            "pid": e.get("pid"),
            "running": bool(e.get("running")),
            "state_age_s": (round(float(e["state_age_s"]), 3)
                            if e.get("state_age_s") is not None else None),
            "equity": e.get("equity"),
            "open_positions": e.get("open_positions"),
            "mode": e.get("mode"),
            "paused": bool(e.get("paused")),
            "crashes": int(e.get("crashes") or 0),
            "restarts": int(e.get("restarts") or 0),
            "exit_code": e.get("exit_code"),
            "backoff_remaining_s": (round(float(e["backoff_remaining_s"]), 3)
                                    if e.get("backoff_remaining_s") is not None
                                    else None),
        } for vid, e in venue_entries.items()},
    }


def _log(msg: str) -> None:
    print(f"[kaos-multibot] {msg}", flush=True)


class Supervisor:
    """Parent: spawn one child per venue, restart dead ones with escalating
    backoff, honor ops-style pause files, write the aggregate state. The
    supervisor itself never touches an exchange."""

    def __init__(self, venues: tuple[str, ...] = VENUES, *,
                 repo: Path | str = REPO,
                 out_dir: Path | str = OUT_DIR,
                 ops_dir: Path | str | None = None,
                 agg_path: Path | str | None = None,
                 poll_s: float = SUPERVISOR_POLL_S,
                 child_cmd: Callable[[str], list[str]] | None = None,
                 spawn: Callable[..., Any] | None = None) -> None:
        self.venues = tuple(venues)
        self.repo = Path(repo)
        self.out_dir = Path(out_dir)
        self.agg_path = Path(agg_path) if agg_path is not None \
            else self.out_dir / AGG_STATE_NAME
        ops_dir = ops_dir if ops_dir is not None \
            else os.environ.get("KAOS_MULTIBOT_OPS_DIR") or OPS_DIR_DEFAULT
        self.ops_dir = Path(ops_dir)
        self.poll_s = float(poll_s)
        self._child_cmd_fn = child_cmd   # test seam (default below)
        self._spawn_fn = spawn or subprocess.Popen   # test seam
        self.meta: dict[str, dict[str, Any]] = {
            vid: {"proc": None, "spawned_mono": None, "crashes": 0,
                  "restarts": 0, "exit_code": None, "backoff_until_mono": 0.0,
                  "paused": False}
            for vid in self.venues
        }

    # ---- child command -----------------------------------------------------
    def child_cmd(self, venue: str) -> list[str]:
        if self._child_cmd_fn is not None:
            return list(self._child_cmd_fn(venue))
        return [sys.executable, str(Path(__file__).resolve()),
                "--venue", venue]

    # ---- state of one venue -------------------------------------------------
    def _venue_entry(self, venue: str, now_mono: float) -> dict[str, Any]:
        m = self.meta[venue]
        proc = m["proc"]
        running = proc is not None and proc.poll() is None
        summary = read_child_summary(self.out_dir / f"state-{venue}.json")
        backoff_rem = max(0.0, m["backoff_until_mono"] - now_mono) \
            if m["proc"] is None else None
        if summary is not None:
            mode = summary["mode"]
        elif running:
            mode = "starting"
        else:
            mode = "paused" if m["paused"] else "down"
        return {
            "pid": proc.pid if running and proc is not None else None,
            "running": running,
            "state_age_s": summary["state_age_s"] if summary else None,
            "equity": summary["equity"] if summary else None,
            "open_positions": (summary["open_positions"] if summary else None),
            "mode": mode,
            "paused": m["paused"],
            "crashes": m["crashes"],
            "restarts": m["restarts"],
            "exit_code": m["exit_code"],
            "backoff_remaining_s": backoff_rem,
        }

    # ---- core loop steps (run_cycle is the testable unit) --------------------
    def _spawn(self, venue: str, now_mono: float) -> None:
        m = self.meta[venue]
        env = build_child_env(os.environ, venue)
        cmd = self.child_cmd(venue)
        try:
            proc = self._spawn_fn(cmd, cwd=str(self.repo), env=env)
        except OSError as exc:
            _log(f"spawn {venue} FAILED ({exc}) — backing off")
            m["crashes"] += 1
            m["backoff_until_mono"] = now_mono + next_restart_delay_s(m["crashes"])
            return
        m["proc"] = proc
        m["spawned_mono"] = now_mono
        m["exit_code"] = None
        _log(f"spawned {venue} child pid={proc.pid} "
             f"(KAOS_VENUE={venue}, crashes={m['crashes']})")

    def run_cycle(self, now_mono: float | None = None) -> dict[str, Any]:
        """One supervision pass: reap dead children, honor pauses, respawn on
        backoff, refresh the aggregate state. Returns the aggregate dict."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        for venue in self.venues:
            m = self.meta[venue]
            proc = m["proc"]
            if proc is not None and proc.poll() is not None:
                lifetime = now - float(m["spawned_mono"] or now)
                code = proc.returncode
                if code == 0:
                    m["crashes"] = 0        # clean exit is not a crash-loop
                else:
                    m["crashes"] = update_crash_streak(m["crashes"], lifetime)
                m["restarts"] += 1
                m["exit_code"] = code
                m["proc"] = None
                m["backoff_until_mono"] = now + next_restart_delay_s(m["crashes"])
                _log(f"{venue} child exited code={code} after "
                     f"{lifetime:.0f}s (crashes={m['crashes']}) — restart in "
                     f"{next_restart_delay_s(m['crashes']):.0f}s")
            if m["proc"] is None:
                if is_paused(self.ops_dir, venue):
                    if not m["paused"]:
                        _log(f"{venue} paused "
                             f"({pause_file_path(self.ops_dir, venue).name})")
                    m["paused"] = True
                else:
                    if m["paused"]:
                        _log(f"{venue} pause released — resuming")
                    m["paused"] = False
                    if now >= m["backoff_until_mono"]:
                        self._spawn(venue, now)
            elif is_paused(self.ops_dir, venue):
                # M-3 fix (review R1): a pause landing on an ALREADY-RUNNING
                # child stops it too (graceful terminate, force-kill only on
                # timeout; the child persists its own final state; never
                # flatten, never delete). m["proc"] stays set until reaped by
                # the normal exit path above — crash accounting is skipped
                # for a pause-stop via the code==0-style clean handoff below.
                proc = m["proc"]
                if proc.poll() is None:
                    _log(f"{venue} pause while running — terminating child")
                    try:
                        proc.terminate()
                        proc.wait(timeout=_TERMINATE_WAIT_S)
                    except Exception:  # noqa: BLE001
                        try:
                            proc.kill()
                        except Exception:  # noqa: BLE001
                            pass
                    m["exit_code"] = proc.returncode
                    m["proc"] = None          # fully reaped, no respawn:
                    m["paused"] = True        # pause branch owns the lane now
                    m["backoff_until_mono"] = 0.0
                    _log(f"{venue} child terminated for pause "
                         f"(code={proc.returncode})")
                m["paused"] = True
        agg = build_aggregate(
            {vid: self._venue_entry(vid, now) for vid in self.venues},
            os.getpid())
        try:
            write_atomic(self.agg_path, agg)
        except Exception as exc:  # noqa: BLE001 — state write never kills parent
            _log(f"aggregate write failed ({exc}) — keeping previous")
        return agg

    def shutdown(self) -> None:
        """Terminate children gracefully (they write their final state on
        KeyboardInterrupt themselves). Never flattens, never deletes state."""
        for venue in self.venues:
            proc = self.meta[venue]["proc"]
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=_TERMINATE_WAIT_S)
                _log(f"{venue} child terminated (code={proc.returncode})")
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                    _log(f"{venue} child killed (terminate timed out)")
                except Exception:  # noqa: BLE001
                    pass
            self.meta[venue]["exit_code"] = proc.returncode
            self.meta[venue]["proc"] = proc

    def run(self) -> None:
        # H-1b: single-instance lock — a second supervisor refuses to start.
        ok, why = acquire_supervisor_lock(self.out_dir)
        if not ok:
            _log(f"supervisor REFUSED: {why}")
            print(f"[kaos-multibot] REFUSED: {why}", file=sys.stderr,
                  flush=True)
            raise SystemExit(3)
        _log(f"supervisor lock: {why}")
        # H-1b: standalone-runner collision gate — if the LIVE standalone
        # runner is still heartbeating, the CRYPTO venue must not start
        # beside it (one mainnet account, two writers = bracket interference).
        # Override: KAOS_MULTIBOT_ALLOW_SHARED=1 (owner-explicit, logged).
        if "crypto" in self.venues:
            active, why = standalone_runner_active(self.out_dir)
            allow = os.environ.get("KAOS_MULTIBOT_ALLOW_SHARED",
                                   "").strip() == "1"
            if active and not allow:
                release_supervisor_lock(self.out_dir)
                _log(f"supervisor REFUSED: standalone runner active — {why}; "
                     "stop it first or set KAOS_MULTIBOT_ALLOW_SHARED=1")
                print("[kaos-multibot] REFUSED: standalone kaos runner is "
                      f"ACTIVE ({why}). Stop it before starting the multibot, "
                      "or set KAOS_MULTIBOT_ALLOW_SHARED=1 to override.",
                      file=sys.stderr, flush=True)
                raise SystemExit(4)
            if active and allow:
                _log("WARNING: KAOS_MULTIBOT_ALLOW_SHARED=1 — starting crypto "
                     f"child beside an active standalone runner ({why})")
        _log(f"supervisor start pid={os.getpid()} venues={','.join(self.venues)} "
             f"ops_dir={self.ops_dir} aggregate={self.agg_path}")
        try:
            while True:
                self.run_cycle()
                time.sleep(self.poll_s)
        except KeyboardInterrupt:
            _log("supervisor stopping (Ctrl+C) — terminating children")
            self.shutdown()
            try:
                self.run_cycle()
            except Exception:  # noqa: BLE001
                pass
            _log("stopped — final aggregate written")
        finally:
            release_supervisor_lock(self.out_dir)


# ============================================================================
# Lazy loaders (children only — the parent never imports these)
# ============================================================================

_LIVE_PAPER_MOD = None
_HARNESS_MOD = None


def _load_live_paper() -> Any:
    """importlib-load scripts/entropy_live_paper.py as a module (READ-ONLY
    reuse: it is never edited). Its own top-level already importlib-loads the
    harness, so the crypto child gets the exact production machinery."""
    global _LIVE_PAPER_MOD
    if _LIVE_PAPER_MOD is None:
        path = REPO / "scripts" / "entropy_live_paper.py"
        spec = importlib.util.spec_from_file_location(
            "entropy_live_paper_multibot", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _LIVE_PAPER_MOD = mod
    return _LIVE_PAPER_MOD


def _load_harness() -> Any:
    """importlib-load scripts/entropy_accuracy_btc15m.py for the PURE helpers
    the equity lane shares with the crypto lane: build_ticks (kline row -> 4
    ticks), utc_day, _BarrierLedger. No behavior of the crypto lane changes —
    this is a read-only second module instance."""
    global _HARNESS_MOD
    if _HARNESS_MOD is None:
        path = REPO / "scripts" / "entropy_accuracy_btc15m.py"
        spec = importlib.util.spec_from_file_location(
            "entropy_accuracy_harness_multibot", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _HARNESS_MOD = mod
    return _HARNESS_MOD


# ============================================================================
# CRYPTO child — byte-identical reuse of the live runner
# ============================================================================

def resolve_crypto_launch(mod: Any, out_dir: Path) -> dict[str, Any]:
    """Resolve the crypto child's launch parameters EXACTLY like the
    standalone runner's main(): same DEFAULT_SYMBOLS parsing, same previous-
    state equity seeding. First boot prefers state-crypto.json; when absent,
    the LIVE state.json is used READ-ONLY as a migration seed (equity base,
    mirror provenance, risk breaker) — the live file is never written."""
    raws = [s.strip().upper() for s in str(mod.DEFAULT_SYMBOLS).split(",")
            if s.strip()]
    prev = mod.load_previous_state(out_dir / CRYPTO_STATE_NAME)
    seeded_from: str | None = None
    if prev is None:
        live = mod.load_previous_state(out_dir / LIVE_STATE_NAME)
        if live is not None:
            prev = live
            seeded_from = str(out_dir / LIVE_STATE_NAME)
    base_cash = 100.0
    if prev is not None:
        eq0 = _f(prev.get("equity_usd"))
        if eq0 > 0.0:
            base_cash = eq0
    return {"raws": raws, "base_cash": base_cash, "prev": prev,
            "seeded_from": seeded_from}


def run_crypto_child(out_dir: Path | str = OUT_DIR, *,
                     once: bool = False) -> Any:
    """The crypto venue worker: the standalone live runner, word for word,
    except the state file is state-crypto.json and env carries KAOS_VENUE."""
    os.environ["KAOS_VENUE"] = "crypto"
    mod = _load_live_paper()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    launch = resolve_crypto_launch(mod, out_dir)
    lp = mod.LivePaper(launch["raws"], launch["base_cash"], out_dir)
    lp.state_path = out_dir / CRYPTO_STATE_NAME
    print(f"[kaos-multibot/crypto] KAOS VENUE=CRYPTO child — standalone "
          f"live config VERBATIM (symbols={','.join(launch['raws'])}, "
          f"cash=${launch['base_cash']:.2f}, state={lp.state_path})",
          flush=True)
    if launch["seeded_from"]:
        print(f"[kaos-multibot/crypto] first boot: restart accounting seeded "
              f"READ-ONLY from {launch['seeded_from']} (live file untouched)",
              flush=True)
    prev = launch["prev"]
    if prev is not None:
        lp.restore_from(prev)
    try:
        lp.start()
        lp.write_state()
        snap = lp.runner.portfolio.snapshot(int(time.time() * 1000) * 1_000_000)
        print(f"[kaos-multibot/crypto] startup done: equity ${snap.equity:.2f}, "
              f"{len(snap.positions)} open, {len(lp.closed_all)} closed trades",
              flush=True)
        if once:
            return lp
        last_write = 0.0
        t0 = time.time()
        last_hb = t0
        while True:
            now_ms = int(time.time() * 1000)
            try:
                lp._reconcile_orphans()
                if lp.poll_bars(now_ms):
                    lp.write_state()
                    last_write = time.time()
            except Exception:  # noqa: BLE001 — same crash-resilience as live
                print("[kaos-multibot/crypto] ERROR in bar poll (will retry):",
                      flush=True)
                traceback.print_exc()
                sys.stdout.flush()
            if time.time() - last_write >= mod.STATE_HEARTBEAT_S:
                try:
                    lp.write_state()
                    last_write = time.time()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            if time.time() - last_hb >= mod.HEARTBEAT_S:
                try:
                    up_s = time.time() - t0
                    hsnap = lp.runner.portfolio.snapshot(
                        int(time.time() * 1000) * 1_000_000)
                    print(f"[kaos-multibot/crypto] [heartbeat] up "
                          f"{int(up_s // 3600)}h {int((up_s % 3600) // 60)}m, "
                          f"equity ${hsnap.equity:.2f}, "
                          f"trades={len(lp.closed_all)}", flush=True)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                last_hb = time.time()
            time.sleep(mod.POLL_S)
    except KeyboardInterrupt:
        try:
            lp.write_state()
        except Exception:  # noqa: BLE001
            pass
        print("[kaos-multibot/crypto] stopped — final state written", flush=True)
        return lp


# ============================================================================
# NASDAQ child — pure-layer equity venue loop
# ============================================================================

def build_equity_cfg(symbols: tuple[str, ...], cash: float,
                     out_dir: Path) -> Any:
    """NASDAQ BotConfig — every knob is the live crypto build_cfg VERBATIM
    (threshold 0.5, min_hold 5, cooldown_bars 4, direction_bars 20, confirm 2,
    trail 0.3, max_hold 192, long_only=False; risk: 10%/trade, max 4
    concurrent, 40% exposure, 15% daily loss, cooldown 180s, 20σ/4σ sigma
    barriers, grace 3, 15m bars). Differences: bare equity symbols (EQUITY
    cost class 2/2 bps by default) and per-venue console/trade file names.
    Per CONTRACT: leverage is NOT a config knob — the Alpaca adapter forces
    1.0 (RegT); the 15% daily-loss and 20σ/4σ barriers stay identical."""
    from entropy.bot.config import (BotConfig, ConsensusConfig,
                                    MarketCostConfig, RiskOverrides)
    return BotConfig(
        mode="paper",
        starting_cash=cash,
        strategies=("consensus",),
        symbols=tuple(symbols),
        ema_symbol=symbols[0],
        ema_fast=9,
        ema_slow=21,
        momentum_min_pct=0.15,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            threshold=0.5,
            exit_mode="trail",
            min_hold_bars=5,
            cooldown_bars=4,
            move_floor=0.0003,
            vote_mode="trend",
            normalize="total",
            min_participation=0.5,
            direction_bars=20,
            confirm_bars=2,
            trail_pct=0.3,
            max_hold_bars=192,
            long_only=False,
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0,
            max_concurrent=4,
            stop_loss_pct=1.5,
            take_profit_pct=1.2,
            max_total_exposure_pct=40.0,
            max_daily_loss_pct=15.0,
            cooldown_s=180.0,
            min_volatility_pct=0.05,
            vol_window_s=900.0,
            stop_mode="sigma",
            stop_sigma_mult=20.0,
            tp_sigma_mult=4.0,
            risk_trail_pct=0.0,
            entry_grace_bars=3,
        ),
        console_log_path=str(Path(out_dir) / "console-nasdaq.log"),
        trade_csv_path=str(Path(out_dir) / "trades-nasdaq.csv"),
    )


def build_equity_executor(*, env: Any = None, pdt_path: Path | str | None = None,
                          calendar: Any = None,
                          transport: Any = None) -> tuple[Any, str, bool]:
    """Build the NASDAQ venue executor. Returns (adapter, banner,
    mirror_enabled). Keys present -> AlpacaEquitiesAdapter (connect_test()
    runs FIRST; failure stays paper with an honest banner). No keys ->
    inert adapter + the frozen 'PAPER (no Alpaca keys)' banner. PdtCounter is
    wired at the venue state dir whenever pdt_path is given."""
    env = env if env is not None else os.environ
    adapter = AlpacaEquitiesAdapter.from_env(env=env, transport=transport)
    if adapter.pdt_counter is None and pdt_path is not None:
        adapter.pdt_counter = PdtCounter(pdt_path, calendar=calendar)
    if not adapter.available:
        return adapter, NASDAQ_BANNER_NOKEYS, False
    ok, detail = adapter.connect_test()
    if ok:
        banner = f"broker connect ok ({detail})"
        return adapter, banner, True
    banner = f"PAPER (alpaca connect FAILED: {detail})"
    return adapter, banner, False


class EquityMirror:
    """Venue mirror for the NASDAQ lane — the LivePaper mirror pattern
    (fail-open, provenance-tracked, bracket attach/adopt) against the
    AlpacaEquitiesAdapter. Inert whenever ``enabled`` is False (no keys,
    failed connect-test, or replay): the paper book stays the sole truth and
    NOTHING is sent to the venue. No auto-flatten anywhere in this lane."""

    def __init__(self, adapter: Any, *, enabled: bool = True) -> None:
        self.adapter = adapter
        self.enabled = bool(enabled)
        # bare symbol -> {qty, order_id, opened_utc, side, stop_id, tp_id,
        #                 sl_px, tp_px}   (crypto mirror_positions schema)
        self.positions: dict[str, dict[str, Any]] = {}
        self.orders_sent = 0
        self.orders_failed = 0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return (self.enabled and self.adapter is not None
                and bool(getattr(self.adapter, "available", False)))

    def _note(self, msg: str) -> None:
        self.last_error = str(msg)[:200]
        print(f"[kaos-multibot/nasdaq] mirror: {self.last_error}", flush=True)

    def mirror_entry(self, bare: str, side: str, qty: float,
                     sl_px: float, tp_px: float) -> None:
        if not self.available:
            return
        try:
            res = self.adapter.place_market_order(bare, side, qty)
            if res.get("skipped"):
                self.last_error = f"{res.get('status')}: {bare}"
                return
            self.orders_sent += 1
            self.positions[bare] = {
                "qty": _f((res or {}).get("qty_used") or qty),
                "order_id": (res or {}).get("order_id"),
                "opened_utc": _utc_now_iso(),
                "side": "long" if side.upper() == "BUY" else "short",
            }
            if sl_px > 0:
                sid = self.adapter.place_venue_stop(bare,
                                                    self.positions[bare]["side"],
                                                    float(sl_px))
                if sid:
                    self.positions[bare]["stop_id"] = str(sid)
            if tp_px > 0:
                tid = self.adapter.place_venue_tp(
                    bare, self.positions[bare]["side"],
                    _f((res or {}).get("qty_used") or qty), float(tp_px))
                if tid:
                    self.positions[bare]["tp_id"] = str(tid)
        except Exception as exc:  # noqa: BLE001 — fail-open at the mirror line
            self.orders_failed += 1
            self._note(f"entry {bare} FAILED: {exc}")

    def mirror_exit(self, bare: str, is_long: bool, qty: float) -> dict | None:
        if not self.available:
            return None
        # exit qty = the REAL opened qty from provenance (crypto parity)
        exit_qty = _f((self.positions.get(bare) or {}).get("qty") or qty)
        side = "SELL" if is_long else "BUY"

        def _cancel_bracket() -> None:
            # H-2 fix (review R1): cancel BOTH legs. The Alpaca close path
            # never cancels brackets by itself (no reduce-only concept) —
            # a surviving GTC stop on a FLAT position would later fill and
            # OPEN an untracked position that rearm cannot re-bracket.
            for leg in ("cancel_venue_stop", "cancel_venue_tp"):
                cancel = getattr(self.adapter, leg, None)
                if callable(cancel):
                    try:
                        cancel(bare)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            res = self.adapter.place_market_order(bare, side, exit_qty,
                                                  reduce_only=True)
            if isinstance(res, dict) and res.get("ok"):
                self.orders_sent += 1
                self.positions.pop(bare, None)
                _cancel_bracket()
            elif isinstance(res, dict) and res.get("skipped"):
                self.last_error = f"{res.get('status')}: {bare}"
                if res.get("status") == "ALREADY_CLOSED":
                    self.positions.pop(bare, None)
                    _cancel_bracket()
            return res if isinstance(res, dict) else None
        except Exception as exc:  # noqa: BLE001
            self.orders_failed += 1
            self._note(f"exit {bare} FAILED: {exc}")
            return None

    def rearm(self, tag: str,
              paper: dict[str, tuple[str, float, float]]) -> None:
        """Bracket re-sync for tracked positions (crypto _venue_rearm
        pattern, compact): gone -> cancel bracket + drop track; stop/TP dead
        -> adopt an open venue order first, else re-place from paper levels.
        ``paper``: bare -> (side, stop_px, tp_px)."""
        if not self.available or not self.positions:
            return
        try:
            real = {str(p.get("symbol") or "").upper().split(":", 1)[-1]: p
                    for p in (self.adapter.get_open_positions() or [])}
        except Exception as exc:  # noqa: BLE001
            self._note(f"{tag} positions: {exc}")
            return
        for bare in list(self.positions):
            mp = self.positions[bare]
            if bare not in real:
                for cancel in (getattr(self.adapter, "cancel_venue_stop", None),
                               getattr(self.adapter, "cancel_venue_tp", None)):
                    if callable(cancel):
                        try:
                            cancel(bare)
                        except Exception:  # noqa: BLE001
                            pass
                self.positions.pop(bare, None)
                print(f"[kaos-multibot/nasdaq] {tag}: {bare} gone on venue — "
                      f"mirror track dropped", flush=True)
                continue
            side = str(mp.get("side") or "long")
            stop_id = str(mp.get("stop_id") or "")
            alive = getattr(self.adapter, "algo_order_alive", None)
            if not (stop_id and callable(alive) and alive(bare, stop_id)):
                adopt = getattr(self.adapter, "adopt_open_stop", None)
                new_id = adopt(bare, side) if callable(adopt) else None
                if not new_id:
                    sl = _f(mp.get("sl_px") or 0.0)
                    place = getattr(self.adapter, "place_venue_stop", None)
                    new_id = (place(bare, side, sl)
                              if callable(place) and sl > 0 else None)
                if new_id:
                    mp["stop_id"] = str(new_id)
            tp_id = str(mp.get("tp_id") or "")
            ord_alive = getattr(self.adapter, "order_alive", None)
            if not (tp_id and callable(ord_alive) and ord_alive(bare, tp_id)):
                adopt_tp = getattr(self.adapter, "adopt_open_tp", None)
                new_tp = adopt_tp(bare, side) if callable(adopt_tp) else None
                if not new_tp:
                    tp = _f(mp.get("tp_px") or 0.0)
                    place_tp = getattr(self.adapter, "place_venue_tp", None)
                    new_tp = (place_tp(bare, side, _f(mp.get("qty") or 0.0), tp)
                              if callable(place_tp) and tp > 0 else None)
                if new_tp:
                    mp["tp_id"] = str(new_tp)


def _closed_identity(rec: dict[str, Any]) -> tuple:
    return (rec["symbol"], rec["side"],
            round(float(rec["entry_price"]), 2),
            round(float(rec["exit_price"]), 2),
            round(float(rec["pnl_usd"]), 2), rec["exit_reason"],
            str(rec["exit_utc"]).split(".")[0])


class EquityVenue:
    """One NASDAQ venue runner: pure BotRunner + ConsensusStrategy +
    RiskManager under a USEquitiesClock gate, with the Alpaca adapter as the
    optional venue mirror. LivePaper-STYLE loop, built WITHOUT touching the
    crypto production path."""

    def __init__(self, *, universe: tuple[str, ...], cash: float,
                 out_dir: Path | str, state_path: Path | str, clock: Any,
                 adapter: Any = None, mirror: EquityMirror | None = None,
                 fetch_bars: Callable[..., list] | None = None,
                 banner: str = NASDAQ_BANNER_NOKEYS,
                 backfill_bars: int = EQUITY_BACKFILL_BARS,
                 idle_heartbeat_s: float = IDLE_HEARTBEAT_S) -> None:
        ha = _load_harness()
        self._ha = ha
        self.raws = tuple(universe)
        self.order = tuple(sorted(self.raws))   # deterministic feed order
        self.syms = {r: r.upper() for r in self.raws}   # bare equity symbols
        self.cfg = build_equity_cfg(tuple(self.syms[r] for r in self.raws),
                                    cash, Path(out_dir))
        self.runner = ha.BotRunner(self.cfg,
                                   run_dir=str(Path(out_dir) / "ledger-nasdaq"))
        self.ledger = ha._BarrierLedger(self.runner)
        self.runner.ledger = self.ledger  # type: ignore[assignment]
        self.clock = clock
        self.adapter = adapter
        self.mirror = mirror
        self._fetch = fetch_bars or (adapter.fetch_bars if adapter is not None
                                     else None)
        self.state_path = Path(state_path)
        self.banner = banner
        self.backfill_bars = int(backfill_bars)
        self.idle_heartbeat_s = float(idle_heartbeat_s)
        self.mode = "IDLE"
        self.session = "CLOSED"
        # bookkeeping (LivePaper parity)
        self.cursor = 0
        self.open_fills: dict[str, tuple[int, Any]] = {}
        self.closed_all: list[dict[str, Any]] = []
        self._closed_keys: set[tuple] = set()
        self.equity_history: list[list[float]] = []
        self.last_close: dict[str, float] = {}
        self.last_fed_open_ms = 0
        self._prev_day: str | None = None
        self._trades_today_base = 0
        self._trades_today_day: str | None = None
        self._warmed = False
        self._mirror_live = False   # Z3-2/Z4-1 parity: the backfill replay
        #                              NEVER mirrors — flipped True only after
        #                              the history feed completes.
        self._last_state_write = 0.0
        self._state_writes = 0
        self._fetch_backoff_s = EQUITY_FETCH_BACKOFF_START_S
        self._last_rearm = 0.0
        self.live_base_equity: float | None = cash
        self._replay_base_equity: float | None = cash
        self._replay_neutralize = False
        self._replay_guard_until_ms = 0
        self._day_anchor_equity: float | None = None
        self.bars_fed = 0

    # ---- tick feeding (LivePaper._feed_bar / _on parity) ---------------------
    def _on(self, t: dict[str, Any]) -> None:
        day = self._ha.utc_day(t["ts_ns"])
        if day != self._prev_day:
            if self._prev_day is not None:
                self.runner.portfolio.reset_day()
                self.runner.risk.reset_day()
            self.runner._utc_day = day
            self._prev_day = day
        self.runner.on_trade(t["symbol"], t["price"], t["amount"],
                             t["side"], t["ts_ns"])

    def _feed_bar(self, by_open: dict[str, dict[int, list]],
                  bar_open_ms: int) -> None:
        """Same convention as the crypto lane: per-symbol slot re-stamping
        (spacing depends on the universe size — never merged universes),
        stop-side-first ordering for shorts."""
        from entropy.bot.portfolio import PositionSide
        self.bars_fed += 1
        if bar_open_ms <= self.last_fed_open_ms:
            return
        spacing = min(10, max(4, 880 // max(1, len(self.order))))
        for j, raw in enumerate(self.order):
            kl = by_open.get(raw, {}).get(bar_open_ms)
            if kl is None:
                continue
            sym = self.syms[raw]
            ticks = self._ha.build_ticks([kl], symbol=sym)
            s = j * spacing
            bar_open_ns = (int(kl[0]) // BAR_MS) * BAR_MS * MS_NS
            for t in ticks:
                off_s = (t["ts_ns"] // NS) % 4
                t["ts_ns"] = bar_open_ns + (s + off_s) * NS
            self._on(ticks[0])
            pos = self.runner.portfolio.positions.get(sym)
            rest = ticks[1:]
            if pos is not None and pos.side is PositionSide.SHORT \
                    and len(rest) == 3:
                h, low = dict(ticks[2]), dict(ticks[1])
                h["ts_ns"] = ticks[0]["ts_ns"] + NS
                low["ts_ns"] = ticks[0]["ts_ns"] + 2 * NS
                rest = [h, low, ticks[3]]
            for t in rest:
                self._on(t)
            self.last_close[raw] = float(kl[4])

    def _bars_by_open(self, klines: list) -> dict[int, list]:
        return {int(k[0]): k for k in klines}

    def _fetch_with_backoff(self, raw: str, limit: int,
                            now_ms: int) -> list:
        attempt = 0
        while True:
            attempt += 1
            try:
                rows = self._fetch(raw, "15m", limit, now_ms)
                self._fetch_backoff_s = EQUITY_FETCH_BACKOFF_START_S
                return rows or []
            except Exception as exc:  # noqa: BLE001 — backoff, never crash
                delay = self._fetch_backoff_s
                print(f"[kaos-multibot/nasdaq] fetch {raw} attempt "
                      f"{attempt}/{EQUITY_FETCH_MAX_ATTEMPTS} failed "
                      f"({exc}) — backoff {delay:.0f}s", flush=True)
                if attempt >= EQUITY_FETCH_MAX_ATTEMPTS:
                    raise
                time.sleep(delay)
                self._fetch_backoff_s = min(delay * 2.0,
                                            EQUITY_FETCH_BACKOFF_MAX_S)

    def _append_equity(self, ts_ms: float, equity: float) -> None:
        ts_ms = float(ts_ms)
        if self._replay_guard_until_ms and ts_ms <= self._replay_guard_until_ms:
            return
        self.equity_history = [p for p in self.equity_history
                               if p[0] != ts_ms]
        self.equity_history.append([ts_ms, _f(equity)])
        excess = len(self.equity_history) - EQUITY_HISTORY_MAX
        if excess > 0:
            del self.equity_history[:excess]

    # ---- session lifecycle ----------------------------------------------------
    def _backfill(self, now_ms: int) -> None:
        """At session open (or first boot inside RTH): pull the 300-bar
        history, feed it (indicator context + position re-derivation) and
        advance the cursor — replay fills are never walked, never recorded,
        never mirrored."""
        total = int(self.backfill_bars)
        per: dict[str, list] = {}
        for raw in self.order:
            per[raw] = self._fetch_with_backoff(raw, total, now_ms)
        n = min((len(v) for v in per.values()), default=0)
        if n == 0:
            print("[kaos-multibot/nasdaq] backfill empty — will retry at the "
                  "next poll", flush=True)
            return
        if any(len(v) != n for v in per.values()):
            per = {r: v[-n:] for r, v in per.items()}
        # LivePaper ordering (load-bearing): the replay barrier (= last replay
        # bar's CLOSE) is armed BEFORE the history feed, so replay-era closed
        # trades register their dedup keys but never become records, and the
        # equity curve never includes replay points. The mirror stays OFF
        # until the feed is done — replay fills can never become orders.
        self._replay_guard_until_ms = max(
            int(v[-1][0]) for v in per.values()) + BAR_MS
        by_open = {r: self._bars_by_open(v) for r, v in per.items()}
        for open_ms in sorted(set(k for m in by_open.values() for k in m)):
            self._feed_bar(by_open, open_ms)
            self.collect_closed()
            eq = self.runner.portfolio.snapshot(
                (open_ms + BAR_MS - 1) * MS_NS).equity
            self._append_equity(open_ms + BAR_MS, eq)
        if self._replay_neutralize:
            self._neutralize_replay_accounting()
        self.cursor = len(self.ledger.fills)
        self.open_fills.clear()
        self.last_fed_open_ms = max(int(v[-1][0]) for v in per.values())
        self._warmed = True
        self._mirror_live = True
        print(f"[kaos-multibot/nasdaq] backfill done: {n} bars x "
              f"{len(self.order)} symbols (guard "
              f"{self._replay_guard_until_ms})", flush=True)

    def _neutralize_replay_accounting(self) -> None:
        """Restart replay neutrality (Z3-1 pattern): the portfolio is rebased
        to the restored equity base so re-derived history never double-counts;
        strategy context and re-derived open positions are kept."""
        p = self.runner.portfolio
        base = self._replay_base_equity
        eq = p.equity()
        delta = (base - eq) if base is not None else 0.0
        if abs(delta) > 1e-9:
            p.starting_cash += delta
        self.runner.risk.reset_day()
        if self._day_anchor_equity is not None:
            p.day_start_equity = self._day_anchor_equity
        else:
            p.day_start_equity = p.equity()
        print(f"[kaos-multibot/nasdaq] replay accounting neutralized: "
              f"${eq:.2f} -> ${p.equity():.2f}", flush=True)

    def _poll(self, now_ms: int) -> bool:
        target_open = ((now_ms - 15_000) // BAR_MS) * BAR_MS
        need = (target_open - self.last_fed_open_ms) // BAR_MS
        if need <= 0:
            return False
        n = int(min(need, 2000))
        by_open: dict[str, dict[int, list]] = {}
        for raw in self.order:
            ks = self._fetch_with_backoff(raw, n, now_ms)
            by_open[raw] = {int(k[0]): k for k in ks
                            if self.last_fed_open_ms < int(k[0]) <= target_open}
        fed = 0
        fed_opens: list[int] = []
        for open_ms in sorted(set(k for m in by_open.values() for k in m)):
            self._feed_bar(by_open, open_ms)
            self.collect_closed()
            eq = self.runner.portfolio.snapshot(
                (open_ms + BAR_MS - 1) * MS_NS).equity
            self._append_equity(open_ms + BAR_MS, eq)
            fed += 1
            fed_opens.append(open_ms)
        if fed_opens:
            self.last_fed_open_ms = max(fed_opens)
        return fed > 0

    # ---- closed-trade pairing + mirror (compact collector) -------------------
    def collect_closed(self) -> None:
        fills = self.ledger.fills
        while self.cursor < len(fills):
            i = self.cursor
            fill, intent = fills[i]
            self.cursor += 1
            kind = str(getattr(intent, "value", intent))
            bare = str(fill.symbol).split(":", 1)[-1].upper()
            is_buy = getattr(fill.side, "value", fill.side) == "buy"
            if kind == "open":
                levels = (self.ledger.open_levels[i]
                          if i < len(self.ledger.open_levels) else None)
                sl = float(levels[0]) if levels else 0.0
                tp = float(levels[1]) if levels else 0.0
                if self._mirror_live and self.mirror is not None \
                        and self.mirror.available:
                    self.mirror.mirror_entry(bare, "BUY" if is_buy else "SELL",
                                             float(fill.qty), sl, tp)
                self.open_fills[fill.symbol] = (i, fill)
                continue
            got = self.open_fills.pop(fill.symbol, None)
            if got is None:
                continue
            oi, entry = got
            is_long = getattr(entry.side, "value", entry.side) == "buy"
            pnl = ((fill.price - entry.price) * fill.qty
                   - entry.fee - fill.fee) if is_long else \
                  ((entry.price - fill.price) * fill.qty
                   - entry.fee - fill.fee)
            guard = self._replay_guard_until_ms
            rec = {
                "symbol": bare,
                "side": "LONG" if is_long else "SHORT",
                "qty": _f(fill.qty),
                "entry_price": _f(entry.price),
                "exit_price": _f(fill.price),
                "pnl_usd": _f(pnl),
                "exit_reason": kind,
                "exit_utc": datetime.fromtimestamp(
                    fill.ts_ns / NS, tz=timezone.utc).isoformat(),
            }
            key = _closed_identity(rec)
            if key in self._closed_keys:
                continue   # regenerated by a restart replay — already persisted
            self._closed_keys.add(key)
            if guard and fill.ts_ns // 1_000_000 < guard:
                continue   # replay-regenerated trade — already persisted
            if self._mirror_live and self.mirror is not None \
                    and self.mirror.available:
                m_exit = self.mirror.mirror_exit(bare, is_long,
                                                 float(fill.qty))
                if isinstance(m_exit, dict) and m_exit.get("ok"):
                    rec["exchange_verified"] = True
                else:
                    rec["exchange_verified"] = False
                    rec["exchange_note"] = self.mirror.last_error or "mirror failed"
            self.closed_all.append(rec)
            excess = len(self.closed_all) - 400
            if excess > 0:
                del self.closed_all[:excess]

    def _rearm_periodic(self, now_ms: int) -> None:
        if self.mirror is None or not self.mirror.available:
            return
        if time.monotonic() - self._last_rearm < EQUITY_RECONCILE_S:
            return
        self._last_rearm = time.monotonic()
        from entropy.bot.portfolio import PositionSide
        paper: dict[str, tuple[str, float, float]] = {}
        for sym, pos in (self.runner.portfolio.positions or {}).items():
            bare = str(sym).split(":", 1)[-1].upper()
            paper[bare] = ("long" if pos.side is PositionSide.LONG
                           else "short",
                           float(getattr(pos, "stop_px", 0) or 0),
                           float(getattr(pos, "tp_px", 0) or 0))
        try:
            self.mirror.rearm("rearm", paper)
        except Exception as exc:  # noqa: BLE001 — fail-open
            print(f"[kaos-multibot/nasdaq] rearm error: {exc}", flush=True)

    # ---- state -------------------------------------------------------------
    def build_state(self) -> dict[str, Any]:
        from entropy.bot.portfolio import PositionSide
        self.collect_closed()
        now_ms = int(time.time() * 1000)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._trades_today_day != today:
            self._trades_today_base = 0
            self._trades_today_day = today
        snap = self.runner.portfolio.snapshot(now_ms * MS_NS)
        positions = []
        for v in snap.positions:
            raw = str(v.symbol).split(":", 1)[-1]
            mark = self.last_close.get(raw, v.mark_px)
            upnl = ((mark - v.entry_px) * v.qty if v.side is PositionSide.LONG
                    else (v.entry_px - mark) * v.qty)
            notional = v.qty * v.entry_px
            positions.append({
                "symbol": raw,
                "side": "LONG" if v.side is PositionSide.LONG else "SHORT",
                "qty": _f(v.qty),
                "entry_price": _f(v.entry_px),
                "mark_price": _f(mark),
                "unrealized_pnl_usd": _f(upnl),
                "notional_usd": _f(notional),
                "sl_price": _f(getattr(v, "stop_px", 0) or 0),
                "tp_price": _f(getattr(v, "tp_px", 0) or 0),
            })
        pdt = None
        pc = getattr(self.adapter, "pdt_counter", None) if self.adapter else None
        if pc is not None:
            try:
                pdt = {"path": str(pc.path),
                       "count_in_window": int(pc.count_in_window(now_ms)),
                       "can_open_trade": bool(pc.can_open_trade(now_ms))}
            except Exception:  # noqa: BLE001 — never block state writes
                pdt = {"path": str(getattr(pc, "path", ""))}
        return {
            "ts_utc": _utc_now_iso(),
            "venue": "nasdaq",
            "mode": self.mode,
            "session": self.session,
            "banner": self.banner,
            "universe": list(self.order),
            "equity_usd": _f(snap.equity),
            "day_pnl_usd": _f(snap.daily_pnl),
            "trades_today": self._trades_today_base + sum(
                1 for t in self.closed_all if t["exit_utc"][:10] == today),
            "positions": positions,
            "equity_history": self.equity_history[-EQUITY_HISTORY_MAX:],
            "live_base_equity": _f(self.live_base_equity
                                   if self.live_base_equity is not None else 0.0),
            "bars_fed": int(self.bars_fed),
            "closed_trades": self.closed_all[-EQUITY_CLOSED_MAX:],
            "mirror": None if self.mirror is None else {
                "available": self.mirror.available,
                "orders_sent": self.mirror.orders_sent,
                "orders_failed": self.mirror.orders_failed,
                "last_error": self.mirror.last_error,
                "host": getattr(self.adapter, "host", None),
                "is_live": bool(getattr(self.adapter, "is_live", False)),
                "connect_note": getattr(self.adapter, "connect_note", None),
                # Z4-2 parity: provenance of OUR mirrored entries survives
                # restarts so rearm adopts instead of double-placing brackets.
                "positions": {k: dict(v)
                              for k, v in self.mirror.positions.items()},
            },
            "pdt": pdt,
            "leverage": 1.0,
        }

    def write_state(self) -> None:
        try:
            write_atomic(self.state_path, self.build_state())
            self._last_state_write = time.monotonic()
            self._state_writes += 1
        except Exception as exc:  # noqa: BLE001 — never crash on state write
            print(f"[kaos-multibot/nasdaq] state write failed ({exc})",
                  flush=True)

    # ---- one gated loop pass -------------------------------------------------
    def run_once(self, now_ms: int, *, force_write: bool = False) -> str:
        """USEquitiesClock gates EVERYTHING: outside RTH = IDLE (no bar
        polling, no venue calls; a heartbeat state write keeps the watchdog
        convinced of liveness). Inside RTH: backfill once, then poll."""
        open_now = bool(self.clock.is_open(now_ms))
        self.session = str(self.clock.session_label(now_ms))
        if not open_now:
            self.mode = "IDLE"
            # 60 s heartbeat while IDLE (watchdog liveness); the first pass
            # always writes because _last_state_write starts at 0.0.
            due = force_write or \
                (time.monotonic() - self._last_state_write) >= self.idle_heartbeat_s
            if due:
                self.write_state()
            return "idle"
        # L-3 fix (review R1): honest mode — "MIRROR" only when a venue
        # broker is actually attached and available; paper-only otherwise
        # (mirror itself may be None in paper-only construction).
        self.mode = ("MIRROR" if self.mirror is not None
                     and self.mirror.available else "PAPER")
        if not self._warmed:
            self._backfill(now_ms)
            self._rearm_periodic(0)   # immediate adopt/sweep pass at open
        else:
            self._poll(now_ms)
            self._rearm_periodic(now_ms)
        self.write_state()
        return "open"

    # ---- restart persistence ---------------------------------------------------
    def restore_from(self, st: dict[str, Any]) -> None:
        """Restore monitor series + counters from the previous state file
        (LivePaper.restore_from pattern). Open positions are NOT restorable —
        the paper book restarts FLAT and the backfill re-derivation + replay
        neutralization keep accounting continuous."""
        hist = st.get("equity_history")
        if isinstance(hist, list):
            for p in hist[-EQUITY_HISTORY_MAX:]:
                try:
                    ts, eq = float(p[0]), float(p[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if math.isfinite(ts) and math.isfinite(eq):
                    self.equity_history.append([ts, eq])
        base = st.get("live_base_equity")
        if base:
            self.live_base_equity = _f(base)
        trades = st.get("closed_trades")
        if isinstance(trades, list):
            for t in trades[-EQUITY_CLOSED_MAX:]:
                if not isinstance(t, dict):
                    continue
                try:
                    rec = {k: t[k] for k in ("symbol", "side", "qty",
                                             "entry_price", "exit_price",
                                             "pnl_usd", "exit_reason",
                                             "exit_utc")}
                except (KeyError, TypeError):
                    continue
                key = _closed_identity(rec)
                if key not in self._closed_keys:
                    self._closed_keys.add(key)
                    self.closed_all.append(rec)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        st_day = str(st.get("ts_utc", ""))[:10]
        try:
            counter = int(st.get("trades_today", 0))
        except (TypeError, ValueError):
            counter = 0
        if st_day == today and counter > 0:
            in_list = sum(1 for t in self.closed_all
                          if str(t["exit_utc"])[:10] == st_day)
            self._trades_today_base = max(counter - in_list, 0)
            self._trades_today_day = today
        try:
            eq0 = _f(st.get("equity_usd"))
        except Exception:  # noqa: BLE001
            eq0 = 0.0
        if eq0 > 0:
            self._replay_base_equity = eq0
            self._replay_neutralize = True
            if st_day == today:
                try:
                    d_pnl = _f(st.get("day_pnl_usd"))
                except Exception:  # noqa: BLE001
                    d_pnl = 0.0
                self._day_anchor_equity = eq0 - d_pnl
        print(f"[kaos-multibot/nasdaq] state restored: equity base ${eq0:.2f}, "
              f"{len(self.closed_all)} closed trades", flush=True)
        # mirror provenance restore (Z4-2): only OUR tracked entries
        mirror_prev = st.get("mirror") or {}
        if isinstance(mirror_prev, dict):
            positions_prev = mirror_prev.get("positions")
            if isinstance(positions_prev, dict) and self.mirror is not None:
                for k, v in positions_prev.items():
                    sk = str(k).upper().split(":", 1)[-1]
                    if sk and isinstance(v, dict) and v:
                        self.mirror.positions[sk] = dict(v)
                if self.mirror.positions:
                    print(f"[kaos-multibot/nasdaq] mirror provenance restored: "
                          f"{sorted(self.mirror.positions)}", flush=True)


def _load_equity_universe(n: int = NASDAQ_UNIVERSE_N) -> tuple[str, ...]:
    from entropy.feeds.equities.universe import LIVE_UNIVERSE
    return tuple(LIVE_UNIVERSE[:n])


def run_nasdaq_child(out_dir: Path | str = OUT_DIR, *,
                     once: bool = False,
                     poll_s: float = EQUITY_POLL_S) -> Any:
    """The NASDAQ venue worker (pure-layer loop; see module docstring)."""
    os.environ["KAOS_VENUE"] = "nasdaq"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / NASDAQ_STATE_NAME
    cash = EQUITY_CASH_DEFAULT
    raw = os.environ.get("KAOS_MULTIBOT_EQUITY_CASH", "")
    try:
        cash = float(raw) if raw.strip() else cash
    except ValueError:
        cash = EQUITY_CASH_DEFAULT
    adapter, banner, mirror_ok = build_equity_executor(
        pdt_path=out_dir / "pdt-fills.json")
    mirror = EquityMirror(adapter, enabled=mirror_ok) if adapter is not None \
        else None
    print(f"[kaos-multibot/nasdaq] KAOS VENUE=NASDAQ child — "
          f"universe={','.join(_load_equity_universe())} cash=${cash:.0f} "
          f"banner={banner!r} state={state_path}", flush=True)
    venue = EquityVenue(universe=_load_equity_universe(), cash=cash,
                        out_dir=out_dir, state_path=state_path,
                        clock=USEquitiesClock(), adapter=adapter,
                        mirror=mirror, banner=banner)
    try:
        prev = json.loads(state_path.read_text(encoding="utf-8")) \
            if state_path.exists() else None
    except (OSError, ValueError):
        prev = None
    if isinstance(prev, dict):
        venue.restore_from(prev)
    try:
        while True:
            try:
                venue.run_once(int(time.time() * 1000))
            except Exception:  # noqa: BLE001 — crash-resilient like the crypto lane
                print("[kaos-multibot/nasdaq] ERROR in loop pass (will retry):",
                      flush=True)
                traceback.print_exc()
                sys.stdout.flush()
            if once:
                break
            time.sleep(poll_s)
    except KeyboardInterrupt:
        venue.write_state()
        print("[kaos-multibot/nasdaq] stopped — final state written",
              flush=True)
    return venue


# ============================================================================
# entrypoint
# ============================================================================

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="KAOS MULTIBOT supervisor (crypto + NASDAQ)")
    ap.add_argument("--venue", choices=VENUES, default=None,
                    help="run as a single-venue child worker")
    ap.add_argument("--venues", default=None,
                    help="parent override: comma-separated venues to supervise")
    ap.add_argument("--ops-dir", default=None,
                    help="pause-file directory (default C:\\botmonitor\\ops)")
    ap.add_argument("--poll", type=float, default=SUPERVISOR_POLL_S,
                    help="supervisor poll interval seconds")
    ap.add_argument("--once", action="store_true",
                    help="children: one startup pass then exit (smoke)")
    args = ap.parse_args(argv)

    if args.venue == "crypto":
        run_crypto_child(once=args.once)
        return
    if args.venue == "nasdaq":
        run_nasdaq_child(once=args.once)
        return

    venues = VENUES
    if args.venues:
        venues = tuple(v.strip().lower() for v in args.venues.split(",")
                       if v.strip().lower() in VENUES) or VENUES
    sup = Supervisor(venues=venues, ops_dir=args.ops_dir, poll_s=args.poll)
    sup.run()


if __name__ == "__main__":
    main()
