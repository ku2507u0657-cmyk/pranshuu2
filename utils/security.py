"""Small security helpers shared by route modules."""

from urllib.parse import urlsplit


def is_safe_redirect_path(target):
    if not target:
        return False

    split = urlsplit(target)
    if split.scheme or split.netloc:
        return False

    return target.startswith("/") and not target.startswith("//")


def safe_redirect_target(target, fallback):
    return target if is_safe_redirect_path(target) else fallback
