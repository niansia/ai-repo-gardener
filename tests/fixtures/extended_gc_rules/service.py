def run():
    return "live"


def _legacy_format(value):
    prefix = "legacy:"
    cleaned = value.strip().upper()
    return f"{prefix}{cleaned}"
