"""Pure helpers for parsing page range and page order specifications."""

from __future__ import annotations



def _parse_page_range(page_spec: str | None, total_pages: int) -> set[int]:
    if not page_spec:
        return set(range(total_pages))

    selected: set[int] = set()
    for part in page_spec.split(","):
        token = part.strip()
        if not token:
            continue

        if "-" in token:
            start_text, end_text = token.split("-", maxsplit=1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Invalid page range: {token}")
            for page_number in range(start, end + 1):
                if page_number < 1 or page_number > total_pages:
                    raise ValueError(f"Page out of bounds: {page_number}")
                selected.add(page_number - 1)
        else:
            page_number = int(token)
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"Page out of bounds: {page_number}")
            selected.add(page_number - 1)

    if not selected:
        raise ValueError("No pages selected.")
    return selected


def _parse_page_order(order_spec: str, total_pages: int) -> list[int]:
    tokens = [part.strip() for part in order_spec.split(",") if part.strip()]
    if not tokens:
        raise ValueError("Page order is required.")

    order: list[int] = []
    for token in tokens:
        page_number = int(token)
        if page_number < 1 or page_number > total_pages:
            raise ValueError(f"Page out of bounds: {page_number}")
        order.append(page_number - 1)

    if len(order) != total_pages:
        raise ValueError(
            f"Page order must contain exactly {total_pages} page numbers (1-based), once each."
        )

    if len(set(order)) != total_pages:
        raise ValueError("Page order must not contain duplicate pages.")

    return order
