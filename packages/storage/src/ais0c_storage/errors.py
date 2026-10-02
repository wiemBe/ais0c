"""Errors raised by the storage package."""


class StorageError(Exception):
    """Base class of the storage errors."""


class ConfigurationError(StorageError):
    """The database settings in the environment are missing or invalid."""


class DuplicateError(StorageError):
    """A row with the same primary key or unique key already exists.

    The insert ran in a savepoint, so the caller's transaction is still usable.
    """


class NotFoundError(StorageError, LookupError):
    """The row to read or change does not exist."""
