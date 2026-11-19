"""Source adapter contracts and registry for vxg-worker."""

from vxg_worker.adapters.base import (
    AdapterParseResult,
    AdapterRegistry,
    BaseSourceAdapter,
    SchemaDriftError,
    SmokeFetchPassthroughAdapter,
    assert_not_soft_404,
)
from vxg_worker.adapters.registry import build_default_adapter_registry

__all__ = [
    "AdapterParseResult",
    "AdapterRegistry",
    "BaseSourceAdapter",
    "SchemaDriftError",
    "SmokeFetchPassthroughAdapter",
    "assert_not_soft_404",
    "build_default_adapter_registry",
]
