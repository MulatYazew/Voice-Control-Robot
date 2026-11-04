"""Keyword-rule baseline parser (deterministic). The implementation lives in the app so that
TEXT MODE uses exactly the same rules that are evaluated here."""

from app.conversation.rules import Intent, parse  # noqa: F401

__all__ = ["Intent", "parse"]
