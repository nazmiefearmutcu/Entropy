from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TypedDict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp

console = Console()


class _UIConfigKwargs(TypedDict, total=False):
    console_log_path: str
    trade_csv_path: str
    equity_source: str


def run_ui(
    console_log: str | None = None,
    trade_csv: str | None = None,
    equity_source: str | None = None,
) -> None:
    """Launch the main Entropy live scanner UI."""
    console.print("[bold yellow]Starting Entropy Live Scanner UI...[/]")
    kwargs: _UIConfigKwargs = {}
    if console_log is not None:
        kwargs["console_log_path"] = console_log
    if trade_csv is not None:
        kwargs["trade_csv_path"] = trade_csv
    if equity_source is not None:
        kwargs["equity_source"] = equity_source
    EntropyApp(AppConfig(**kwargs)).run()

def run_bot(argv: list[str]) -> None:
    """Launch the trading bot runner."""
    from entropy.bot.__main__ import main as bot_main
    bot_main(argv)

def run_calibrate(args: argparse.Namespace) -> None:
    """Run parameter calibration and accuracy tests on back and forward tests."""
    from entropy.bot.calibration import calibrate_and_test
    
    console.print(
        Panel("[bold green]ENTROPY TRADING BOT CALIBRATION & ACCURACY TESTS[/]", expand=False)
    )
    console.print(
        f"Running calibration with [cyan]{args.ticks_back}[/] backtest ticks and "
        f"[cyan]{args.ticks_forward}[/] forward test ticks (Seed: {args.seed})..."
    )
    
    res = calibrate_and_test(
        n_ticks_back=args.ticks_back,
        n_ticks_forward=args.ticks_forward,
        seed=args.seed
    )
    
    # Selected Symbols
    console.print("\n[bold]Selected Random Symbols for Evaluation:[/]")
    console.print(f"  ● Equities: {', '.join(res['symbols']['equities'])}")
    console.print(f"  ● Crypto:   {', '.join(res['symbols']['crypto'])}")
    
    # Best Parameters
    params = res["best_params"]
    param_table = Table(
        title="Calibrated Optimal Parameters", show_header=True, header_style="bold magenta"
    )
    param_table.add_column("Parameter", style="cyan")
    param_table.add_column("Optimal Value", style="green")
    
    param_table.add_row("EMA Fast Period", str(params["fast"]))
    param_table.add_row("EMA Slow Period", str(params["slow"]))
    param_table.add_row("Momentum Min Pct Threshold", f"{params['min_pct']:.2f}%")
    param_table.add_row("Risk Stop Loss Pct", f"{params['stop_loss_pct']:.2f}%")
    param_table.add_row("Risk Take Profit Pct", f"{params['take_profit_pct']:.2f}%")
    console.print(param_table)

    # Back vs Forward Results Table
    back = res["back_results"]
    fwd = res["forward_results"]
    
    results_table = Table(
        title="Accuracy Performance (Backtest vs Forward Test)",
        show_header=True,
        header_style="bold blue",
    )
    results_table.add_column("Metric", style="bold")
    results_table.add_column("Backtest (In-Sample)", style="green")
    results_table.add_column("Forward Test (Out-of-Sample)", style="yellow")
    
    results_table.add_row("Initial Equity", "$100,000.00", "$100,000.00")
    results_table.add_row(
        "Final Equity", f"${back['final_equity']:,.2f}", f"${fwd['final_equity']:,.2f}"
    )
    
    ret_style_back = "green" if back["total_return"] >= 0 else "red"
    ret_style_fwd = "green" if fwd["total_return"] >= 0 else "red"
    results_table.add_row(
        "Total Return %",
        f"[{ret_style_back}]{back['total_return']:+.2%}[/]",
        f"[{ret_style_fwd}]{fwd['total_return']:+.2%}[/]",
    )
    
    results_table.add_row("Total Trades", str(back["total_trades"]), str(fwd["total_trades"]))
    results_table.add_row("Win Rate %", f"{back['win_rate']:.2%}", f"{fwd['win_rate']:.2%}")
    results_table.add_row(
        "Profit Factor", f"{back['profit_factor']:.2f}", f"{fwd['profit_factor']:.2f}"
    )
    results_table.add_row(
        "Annualized Sharpe Ratio", f"{back['sharpe']:.2f}", f"{fwd['sharpe']:.2f}"
    )
    
    console.print(results_table)
    _print_disclaimer()
    console.print("[bold green]✔ Calibration & Accuracy test runs complete.[/]\n")


def _print_disclaimer() -> None:
    from entropy.bot.calibration import DISCLAIMER

    console.print(f"[bold yellow]⚠ {DISCLAIMER}[/]")


def run_walk_forward(args: argparse.Namespace) -> None:
    """Run walk-forward K-fold calibration and render per-fold OOS metrics."""
    from entropy.bot import calibration

    console.print(
        Panel("[bold green]ENTROPY WALK-FORWARD K-FOLD CALIBRATION[/]", expand=False)
    )
    console.print(
        f"Running [cyan]{args.walk_forward}[/] walk-forward folds "
        f"(Seed: {args.seed})..."
    )
    res = calibration.walk_forward(n_folds=args.walk_forward, seed=args.seed)

    console.print("\n[bold]Selected Random Symbols for Evaluation:[/]")
    console.print(f"  ● Equities: {', '.join(res['symbols']['equities'])}")
    console.print(f"  ● Crypto:   {', '.join(res['symbols']['crypto'])}")

    table = Table(
        title=f"Walk-Forward OOS Performance ({res['n_folds']} folds, "
              f"{res['n_ticks']} ticks)",
        show_header=True,
        header_style="bold blue",
    )
    table.add_column("Fold", style="bold")
    table.add_column("Train Range", style="cyan")
    table.add_column("Eval Range", style="cyan")
    table.add_column("Chosen Params")
    table.add_column("OOS Return", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Trades", justify="right")

    for fold in res["folds"]:
        p = fold["params"]
        oos = fold["oos"]
        (t0, t1), (e0, e1) = fold["train_range"], fold["eval_range"]
        ret_style = "green" if oos["total_return"] >= 0 else "red"
        table.add_row(
            str(fold["fold"]),
            f"[{t0}, {t1})",
            f"[{e0}, {e1})",
            f"ema {p['fast']}/{p['slow']} mom {p['min_pct']:.2f} "
            f"cons {p['threshold']:.2f}@{p['bar_s']:g}s "
            f"sl/tp {p['stop_loss_pct']:.1f}/{p['take_profit_pct']:.1f}",
            f"[{ret_style}]{oos['total_return']:+.2%}[/]",
            f"{oos['win_rate']:.2%}",
            f"{oos['profit_factor']:.2f}",
            f"{oos['sharpe']:.2f}",
            str(oos["total_trades"]),
        )

    agg = res["aggregate"]
    agg_style = "green" if agg["mean_return"] >= 0 else "red"
    table.add_section()
    table.add_row(
        "[bold]AGG[/]",
        "—",
        "—",
        f"{agg['distinct_param_sets']} distinct param set(s)",
        f"[{agg_style}]{agg['mean_return']:+.2%}[/]",
        f"{agg['mean_win_rate']:.2%}",
        "—",
        f"{agg['mean_sharpe']:.2f}",
        str(agg["total_oos_trades"]),
    )
    console.print(table)

    worst_style = "green" if agg["worst_fold_return"] >= 0 else "red"
    console.print(
        f"[bold]Median OOS return:[/] {agg['median_return']:+.2%}   "
        f"[bold {worst_style}]WORST fold: #{agg['worst_fold']} at "
        f"{agg['worst_fold_return']:+.2%}[/]   "
        f"[bold]Param stability:[/] {agg['distinct_param_sets']}/{res['n_folds']} "
        f"distinct sets chosen"
    )
    _print_disclaimer()
    console.print("[bold green]✔ Walk-forward calibration complete.[/]\n")


def run_benchmark() -> None:
    """Run speed benchmarks."""
    from entropy.bot.benchmark import SpeedBenchmark
    
    console.print(
        Panel("[bold yellow]ENTROPY PERFORMANCE & THROUGHPUT BENCHMARKS[/]", expand=False)
    )
    
    console.print("Measuring Engine on_trade throughput (250k ticks)...")
    engine_tps = SpeedBenchmark.run_engine_throughput()
    console.print(f"  ● Engine throughput: [green]{engine_tps:,.0f} ticks/second[/]")
    
    console.print("Measuring Candle Aggregator throughput (500k ticks)...")
    candle_tps = SpeedBenchmark.run_candle_aggregator()
    console.print(f"  ● Candle Aggregator: [green]{candle_tps:,.0f} ticks/second[/]")
    
    console.print("Measuring Full Trading Bot pipeline throughput (100k ticks)...")
    full_tps = SpeedBenchmark.run_full_bot_pipeline()
    console.print(f"  ● Full Trading Bot pipeline: [green]{full_tps:,.0f} ticks/second[/]")
    
    # Benchmarks summary table
    bench_table = Table(title="Throughput Summary", show_header=True, header_style="bold cyan")
    bench_table.add_column("Component", style="bold")
    bench_table.add_column("Speed (Ticks/sec)", style="green")
    bench_table.add_column("Status", style="bold green")
    
    bench_table.add_row(
        "Event Engine (on_trade)",
        f"{engine_tps:,.0f}",
        "Excellent" if engine_tps > 200000 else "Pass",
    )
    bench_table.add_row(
        "Candle Aggregation",
        f"{candle_tps:,.0f}",
        "Excellent" if candle_tps > 300000 else "Pass",
    )
    bench_table.add_row(
        "Full System Loop",
        f"{full_tps:,.0f}",
        "Excellent" if full_tps > 50000 else "Pass",
    )
    
    console.print("\n")
    console.print(bench_table)
    console.print("[bold green]✔ Benchmarks complete.[/]\n")

def main(argv: Sequence[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
        
    parser = argparse.ArgumentParser(
        prog="entropy",
        description="Entropy: Real-time Terminal Market Scanner, Algo Console, and Trading Bot."
    )
    parser.add_argument("--console-log", default=None, help="Path to write console log output")
    parser.add_argument("--trade-csv", default=None, help="Path to write trade CSV report")
    parser.add_argument("--equity-source", choices=["sim", "live", "auto"], default=None,
                        help="equity feed source (auto = live while the US market is open)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # UI command
    ui_parser = subparsers.add_parser("ui", help="Launch the main TUI scanner dashboard (default)")
    ui_parser.add_argument(
        "--console-log", default=argparse.SUPPRESS, help="Path to write console log output"
    )
    ui_parser.add_argument(
        "--trade-csv", default=argparse.SUPPRESS, help="Path to write trade CSV report"
    )
    ui_parser.add_argument("--equity-source", choices=["sim", "live", "auto"],
                           default=argparse.SUPPRESS,
                           help="equity feed source (auto = live while the US market is open)")
    
    # Bot command
    bot_parser = subparsers.add_parser("bot", help="Run the automated trade bot CLI/TUI")
    bot_parser.add_argument(
        "--console-log", default=argparse.SUPPRESS, help="Path to write console log output"
    )
    bot_parser.add_argument(
        "--trade-csv", default=argparse.SUPPRESS, help="Path to write trade CSV report"
    )
    bot_parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    bot_parser.add_argument(
        "--risk", default="medium", type=str.lower,
        choices=["frosty", "medium", "extreme"],
        help="frosty | medium | extreme"
    )
    bot_parser.add_argument("--cash", type=float, default=100_000.0)
    bot_parser.add_argument("--no-crypto", action="store_true", help="disable the live crypto feed")
    bot_parser.add_argument("--dashboard", action="store_true", help="run the TUI dashboard")
    bot_parser.add_argument("--i-understand-the-risk", action="store_true",
                            help="required to even attempt live trading")
    
    # Calibrate command
    cal_parser = subparsers.add_parser(
        "calibrate", help="Calibrate strategies & run accuracy back/forward tests"
    )
    cal_parser.add_argument(
        "--ticks-back", type=int, default=15000, help="number of backtest ticks"
    )
    cal_parser.add_argument(
        "--ticks-forward", type=int, default=15000, help="number of forward test ticks"
    )
    cal_parser.add_argument(
        "--seed", type=int, default=42, help="random seed for symbol selection & simulation"
    )
    cal_parser.add_argument(
        "--walk-forward", type=int, default=None, metavar="N",
        help="run walk-forward K-fold OOS calibration with N folds (N >= 2) "
             "instead of the single back/forward split"
    )
    
    # Benchmark command
    subparsers.add_parser("benchmark", help="Run system throughput & latency benchmarks")
    
    args = parser.parse_args(argv)
    
    if args.command == "bot":
        # rebuild argv list for the bot module: mode/risk/cash are always set
        # (argparse defaults), so forward them unconditionally — a truthiness
        # guard would silently drop legitimate falsy values like `--cash 0`.
        bot_args = ["--mode", args.mode, "--risk", args.risk, "--cash", str(args.cash)]
        if args.no_crypto:
            bot_args.append("--no-crypto")
        if args.dashboard:
            bot_args.append("--dashboard")
        if args.i_understand_the_risk:
            bot_args.append("--i-understand-the-risk")
        console_log = getattr(args, "console_log", None)
        trade_csv = getattr(args, "trade_csv", None)
        if console_log is not None:
            bot_args.extend(["--console-log", console_log])
        if trade_csv is not None:
            bot_args.extend(["--trade-csv", trade_csv])
        run_bot(bot_args)
    elif args.command == "calibrate":
        if args.walk_forward is not None:
            if args.walk_forward < 2:
                parser.error(
                    f"--walk-forward requires N >= 2, got {args.walk_forward}"
                )
            run_walk_forward(args)
        else:
            run_calibrate(args)
    elif args.command == "benchmark":
        run_benchmark()
    else:
        # Default command: launch the main UI
        console_log = getattr(args, "console_log", None)
        trade_csv = getattr(args, "trade_csv", None)
        equity_source = getattr(args, "equity_source", None)
        run_ui(console_log=console_log, trade_csv=trade_csv, equity_source=equity_source)

if __name__ == "__main__":
    main()
