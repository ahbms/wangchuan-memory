#!/usr/bin/env python3
"""
存续层 - 退役兼容壳

Standalone note:
- In the original Tiangong tree this module re-exported runtime.energy.
- In the standalone package, L4 runtime is optional and unavailable by default.
- This file keeps old import paths from crashing, but does not provide real energy accounting.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict


class EnergyState(Enum):
    DISABLED = "disabled"
    NORMAL = "normal"


@dataclass
class EnergyConfig:
    enabled: bool = False


class EnergySystem:
    def __init__(self, *args, **kwargs):
        self.config = EnergyConfig(enabled=False)

    def get_state(self) -> Dict[str, Any]:
        return {"enabled": False, "state": EnergyState.DISABLED.value, "source": "standalone_stub"}


__all__ = ["EnergySystem", "EnergyState", "EnergyConfig"]


if __name__ == "__main__":
    print("energy_system retired; standalone package uses a no-op compatibility shell")
