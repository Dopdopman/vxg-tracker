"""Gold commodity adapters."""

from vxg_worker.adapters.gold.cafef_gold import CafefGoldCrossCheckAdapter
from vxg_worker.adapters.gold.doji_board import DojiBoardAdapter
from vxg_worker.adapters.gold.pnj_html import PnjHtmlAdapter
from vxg_worker.adapters.gold.sjc_json import SjcJsonAdapter

__all__ = [
    "CafefGoldCrossCheckAdapter",
    "DojiBoardAdapter",
    "PnjHtmlAdapter",
    "SjcJsonAdapter",
]

