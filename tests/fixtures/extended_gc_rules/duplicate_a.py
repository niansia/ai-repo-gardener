def normalize(value):
    cleaned = value.strip()
    if not cleaned:
        return ""
    return cleaned.lower()
