def normalize(candidate):
    text = candidate.strip()
    if not text:
        return ""
    return text.lower()
