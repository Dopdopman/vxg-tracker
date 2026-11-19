"""Fuel commodity adapters."""

from vxg_worker.adapters.fuel.petrolimex_fuel import PetrolimexFuelAdapter
from vxg_worker.adapters.fuel.saigonpetro_fuel import SaigonPetroFuelAdapter
from vxg_worker.adapters.fuel.webgia_fuel import WebgiaFuelCrossCheckAdapter

__all__ = [
    "PetrolimexFuelAdapter",
    "SaigonPetroFuelAdapter",
    "WebgiaFuelCrossCheckAdapter",
]

