
from typing import List, Optional


def normalize(values: List[Optional[str]]) -> List[str]:
    """Normalize all values in a comprehensive and robust way."""
    result = []
    for value in values:
        if value is not None:
            result.append(value.strip())
    return result
