"""Default adapter registry wiring all 11 Vietnam Gold, Fuel, and Gas adapters."""

from __future__ import annotations

from vxg_worker.adapters.base import AdapterRegistry
from vxg_worker.adapters.fuel import (
    PetrolimexFuelAdapter,
    SaigonPetroFuelAdapter,
    WebgiaFuelCrossCheckAdapter,
)
from vxg_worker.adapters.gas import (
    PetrolimexGasAdapter,
    PvGasLpgAdapter,
    SaigonPetroGasAdapter,
    VietnamBizGasCrossCheckAdapter,
)
from vxg_worker.adapters.gold import (
    CafefGoldCrossCheckAdapter,
    DojiBoardAdapter,
    PnjHtmlAdapter,
    SjcJsonAdapter,
)


def build_default_adapter_registry() -> AdapterRegistry:
    """Register all Tier 1 and Tier 2 commodity source adapters."""
    registry = AdapterRegistry()
    for adapter in (
        SjcJsonAdapter(),
        DojiBoardAdapter(),
        PnjHtmlAdapter(),
        CafefGoldCrossCheckAdapter(),
        PetrolimexFuelAdapter(),
        SaigonPetroFuelAdapter(),
        WebgiaFuelCrossCheckAdapter(),
        PetrolimexGasAdapter(),
        SaigonPetroGasAdapter(),
        PvGasLpgAdapter(),
        VietnamBizGasCrossCheckAdapter(),
    ):
        registry.register(adapter)
    return registry

