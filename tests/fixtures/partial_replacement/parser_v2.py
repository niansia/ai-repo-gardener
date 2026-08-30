def parse(value: str) -> str:
    return value.strip()


def parse_legacy_bytes(value: bytes) -> str:
    return value.decode("utf-8")
