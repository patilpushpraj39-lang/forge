VALID_STATUSES = {"active", "paused", "disabled"}


def normalize_status(value: str) -> str:
    return value


def is_valid_status(value: str) -> bool:
    return normalize_status(value) in VALID_STATUSES
