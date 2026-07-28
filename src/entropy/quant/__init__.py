"""Quantitative primitives shared by the bot's strategies and risk layer.

Pure and I/O-free by design: `conventions` knows the calendar, `pricing` knows
the arithmetic, `vol` knows the estimator, `distribution` knows the risk
translation. Nothing here opens a socket or reads a store.
"""
