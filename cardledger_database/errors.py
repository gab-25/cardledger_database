class SourceError(Exception):
    """A public data source could not be downloaded or did not have the expected shape."""


class QualityError(Exception):
    """A generated catalog failed a check and must not be published."""
