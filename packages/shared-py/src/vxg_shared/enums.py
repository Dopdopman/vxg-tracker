"""Canonical StrEnums shared across DB models, FastAPI, and Scraper Worker."""

from enum import StrEnum


class LocationType(StrEnum):
    CITY = "CITY"
    FUEL_ZONE = "FUEL_ZONE"


class MacroRegion(StrEnum):
    NORTH = "NORTH"
    CENTRAL = "CENTRAL"
    SOUTH = "SOUTH"
    NATIONAL = "NATIONAL"


class FuelZoneCode(StrEnum):
    VUNG_1 = "VUNG_1"
    VUNG_2 = "VUNG_2"


class CommodityCategory(StrEnum):
    GOLD = "GOLD"
    FUEL = "FUEL"
    GAS = "GAS"


class PriceUnit(StrEnum):
    VND_PER_LUONG = "VND_PER_LUONG"
    VND_PER_LITER = "VND_PER_LITER"
    VND_PER_CYLINDER = "VND_PER_CYLINDER"


class PricingType(StrEnum):
    BUY_SELL = "BUY_SELL"
    SINGLE_RETAIL = "SINGLE_RETAIL"


class SourceTier(StrEnum):
    PRIMARY_OFFICIAL = "PRIMARY_OFFICIAL"
    SECONDARY_CROSSCHECK = "SECONDARY_CROSSCHECK"


class FetchStrategy(StrEnum):
    HTTP_XML = "HTTP_XML"
    HTTP_JSON = "HTTP_JSON"
    HTTP_HTML = "HTTP_HTML"
    PLAYWRIGHT_JS = "PLAYWRIGHT_JS"


class CrawlScope(StrEnum):
    ALL = "ALL"
    GOLD = "GOLD"
    FUEL = "FUEL"
    GAS = "GAS"


class TriggerType(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL_ADMIN = "MANUAL_ADMIN"
    RETRY = "RETRY"


class CrawlRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class FetchStatus(StrEnum):
    SUCCESS = "SUCCESS"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    BLOCKED = "BLOCKED"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CROSS_CHECKED = "CROSS_CHECKED"
    FLAGGED = "FLAGGED"
    REJECTED = "REJECTED"
    OVERRIDDEN_MANUAL = "OVERRIDDEN_MANUAL"


class AlertType(StrEnum):
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    SOURCE_DOWN_3X = "SOURCE_DOWN_3X"
    PRICE_SPIKE = "PRICE_SPIKE"
    CROSS_CHECK_MISMATCH = "CROSS_CHECK_MISMATCH"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class AlertSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class SeriesOrigin(StrEnum):
    BACKFILL = "BACKFILL"
    LIVE_ROLLUP = "LIVE_ROLLUP"


class MacroIndicatorCode(StrEnum):
    XAU_USD = "XAU_USD"
    BRENT_USD = "BRENT_USD"
    WTI_USD = "WTI_USD"
    DXY = "DXY"
    USD_VND_VCB = "USD_VND_VCB"


class NewsDomain(StrEnum):
    GOLD = "GOLD"
    FUEL = "FUEL"
    GAS = "GAS"
    MACRO = "MACRO"


class NewsImpactLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class NewsScoringEngine(StrEnum):
    LEXICON_V1 = "LEXICON_V1"
    LEXICON_PLUS_GEMINI = "LEXICON_PLUS_GEMINI"


class ForecastHorizon(StrEnum):
    T_PLUS_1 = "T_PLUS_1"
    T_PLUS_7 = "T_PLUS_7"
    NEXT_FUEL_CYCLE = "NEXT_FUEL_CYCLE"
    NEXT_GAS_MONTH = "NEXT_GAS_MONTH"


class ForecastStatus(StrEnum):
    READY = "READY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class PredictedDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"

