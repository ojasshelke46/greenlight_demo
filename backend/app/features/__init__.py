"""Imports every feature package so each one's __init__ registers its hooks at startup."""

from app.features import fleet, policy, proof  # noqa: F401
