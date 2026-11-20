"""Ownership of a borrowed read-only SQLite snapshot connection."""


class BorrowedConnection:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        """The snapshot owner closes the connection after all leases finish."""
