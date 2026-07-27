from importlib.metadata import PackageNotFoundError, metadata

try:
    __version__ = metadata("ymodterm")["Version"]
except PackageNotFoundError:
    __version__ = "unknown"
