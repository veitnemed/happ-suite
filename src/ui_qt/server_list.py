"""Qt model for provider nodes; selecting a row never changes the VPN route."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


@dataclass(frozen=True)
class ServerItem:
    name: str
    country: str = ""
    protocol: str = ""
    delay_ms: float | None = None


class ServerListModel(QAbstractListModel):
    NameRole = int(Qt.ItemDataRole.UserRole) + 1
    CountryRole = NameRole + 1
    ProtocolRole = NameRole + 2
    DelayRole = NameRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[ServerItem] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole or role == self.NameRole:
            return item.name
        if role == self.CountryRole:
            return item.country
        if role == self.ProtocolRole:
            return item.protocol
        if role == self.DelayRole:
            return item.delay_ms
        return None

    def roleNames(self):
        return {
            self.NameRole: b"name",
            self.CountryRole: b"country",
            self.ProtocolRole: b"protocol",
            self.DelayRole: b"delayMs",
        }

    def set_items(self, items):
        self.beginResetModel()
        self._items = [
            item if isinstance(item, ServerItem) else ServerItem(
                name=str(item.get("name", "")),
                country=str(item.get("country", "")),
                protocol=str(item.get("type", item.get("protocol", ""))),
                delay_ms=item.get("delay_ms"),
            )
            for item in items
            if (item.name if isinstance(item, ServerItem) else item.get("name"))
        ]
        self.endResetModel()

    def item_at(self, row: int) -> ServerItem | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._items)
