"""
Confirmation gate for autonomous code changes.

Every write operation passes through this module before files
are modified. It displays the planned actions and asks the user
whether execution should continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class PlannedAction:
    title: str
    details: list[str]


def confirm_actions(actions: Iterable[PlannedAction], *, yolo: bool = False) -> bool:
    """
    Return True if execution should continue.

    - yolo=True skips confirmation.
    - Interactive mode asks the user.
    """

    if yolo:
        return True

    print("\nKuza is about to:\n")

    for action in actions:
        print(f"• {action.title}")
        for item in action.details:
            print(f"    - {item}")

    while True:
        answer = input("\nProceed? (y/N): ").strip().lower()

        if answer in ("y", "yes"):
            return True

        if answer in ("", "n", "no"):
            return False

        print("Please answer y or n.")
