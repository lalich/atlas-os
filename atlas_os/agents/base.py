"""Base types for local agent definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    division: str
    description: str
    enabled: bool = False

