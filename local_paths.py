"""Local image paths use the host OS, never a fixed container location."""
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def local_image_path(source: str) -> Path:
    if source.lower().startswith('file:'):
        parts = urlsplit(source)
        if parts.netloc.lower() not in ('', 'localhost') or parts.query or parts.fragment:
            raise ValueError('Only local file URLs are accepted')
        source = url2pathname(parts.path)
    # Do not access network shares, including Windows device/UNC paths.
    if source.startswith(('\\\\', '//')):
        raise ValueError('Network paths are not accepted')
    path = Path(source)
    if not path.is_absolute():
        raise ValueError('An absolute local path is required')
    return path.resolve()
