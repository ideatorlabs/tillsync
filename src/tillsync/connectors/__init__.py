"""Built-in connectors. Importing this package registers them by name."""

from __future__ import annotations

from .csv_file import CsvConnector, MappingError
from .gsheets import GoogleSheetsConnector, SheetNotPublicError, to_csv_url
from .http_json import ConnectorError, HttpJsonConnector, dig, map_customer, map_transaction
from .mock import MockConnector
from .square import SquareConnector

__all__ = [
    "CsvConnector",
    "MappingError",
    "GoogleSheetsConnector",
    "SheetNotPublicError",
    "to_csv_url",
    "HttpJsonConnector",
    "ConnectorError",
    "dig",
    "map_customer",
    "map_transaction",
    "MockConnector",
    "SquareConnector",
]
