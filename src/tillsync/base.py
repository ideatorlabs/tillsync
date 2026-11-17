"""The connector contract and the registry that names connectors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Protocol, TypeVar, runtime_checkable

from .models import ImportReport, RawCustomer, RawTransaction

__all__ = ["Connector", "BaseConnector", "register", "create", "available", "get_connector_class"]


@runtime_checkable
class Connector(Protocol):
    """Anything that can list customers and transactions.

    ``since`` is an aware UTC datetime or None. Connectors should return records whose
    ``cursor_time`` is at or after ``since`` (``>=``); :func:`tillsync.sync` removes the
    ones it already delivered. Returning older records is allowed but wasteful.
    """

    def test_connection(self) -> bool: ...

    def fetch_customers(self, since: datetime | None = None) -> Iterable[RawCustomer]: ...

    def fetch_transactions(self, since: datetime | None = None) -> Iterable[RawTransaction]: ...


class BaseConnector:
    """Convenience base class: subclass it, set ``name``, implement what your source has.

    ``report`` collects what was skipped and why during the last fetch.
    """

    name: str = "custom"

    def __init__(self) -> None:
        self.report = ImportReport()

    def test_connection(self) -> bool:
        return True

    def fetch_customers(self, since: datetime | None = None) -> Iterable[RawCustomer]:
        return []

    def fetch_transactions(self, since: datetime | None = None) -> Iterable[RawTransaction]:
        return []


_REGISTRY: dict[str, Callable[..., Connector]] = {}
C = TypeVar("C", bound=Callable[..., Any])


def register(name: str) -> Callable[[C], C]:
    """Class decorator that makes a connector creatable by name with :func:`create`."""

    def deco(cls: C) -> C:
        key = name.strip().lower()
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise ValueError(f"a connector named {key!r} is already registered")
        _REGISTRY[key] = cls
        return cls

    return deco


def get_connector_class(name: str) -> Callable[..., Connector]:
    try:
        return _REGISTRY[name.strip().lower()]
    except KeyError:
        raise KeyError(f"no connector named {name!r}; available: {', '.join(available())}") from None


def create(name: str, /, **options: Any) -> Connector:
    """Build a registered connector: ``create("csv", path="sales.csv")``."""
    return get_connector_class(name)(**options)


def available() -> list[str]:
    """Names of every registered connector."""
    return sorted(_REGISTRY)
