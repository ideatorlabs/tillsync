"""TillSync: pull customers and sales from POS systems, APIs and spreadsheets into one shape."""

from __future__ import annotations

from .automap import AutoMapping, FieldGuess, automap
from .base import BaseConnector, Connector, available, create, register
from .connectors import (
    ConnectorError,
    CsvConnector,
    GoogleSheetsConnector,
    HttpJsonConnector,
    MappingError,
    MockConnector,
    SheetNotPublicError,
    SquareConnector,
    map_customer,
    map_transaction,
    to_csv_url,
)
from .lines import attach_line_items, read_line_items
from .models import Cursor, ImportReport, LineItem, RawCustomer, RawTransaction, StreamCursor, to_jsonable
from .money import detect_currency, parse_money
from .names import clean_person_name, reads_like_name
from .netguard import BlockedURLError, check_url, guarded_client
from .phone import is_masked_phone, normalize_phone, phone_key
from .sync import Batch, SyncResult, sync
from .tables import Table, parse_csv_text, read_table
from .times import parse_datetime

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # records
    "RawCustomer",
    "RawTransaction",
    "LineItem",
    "ImportReport",
    "Cursor",
    "StreamCursor",
    "to_jsonable",
    # connectors
    "Connector",
    "BaseConnector",
    "register",
    "create",
    "available",
    "CsvConnector",
    "GoogleSheetsConnector",
    "HttpJsonConnector",
    "SquareConnector",
    "MockConnector",
    "map_customer",
    "map_transaction",
    "to_csv_url",
    "ConnectorError",
    "MappingError",
    "SheetNotPublicError",
    # sync
    "sync",
    "Batch",
    "SyncResult",
    # mapping and parsing
    "automap",
    "AutoMapping",
    "FieldGuess",
    "Table",
    "read_table",
    "parse_csv_text",
    "read_line_items",
    "attach_line_items",
    "parse_money",
    "detect_currency",
    "parse_datetime",
    "normalize_phone",
    "phone_key",
    "is_masked_phone",
    "clean_person_name",
    "reads_like_name",
    # network safety
    "check_url",
    "guarded_client",
    "BlockedURLError",
]
