"""Render report text from values that were already computed."""


def fmt(value) -> str:
    if value is None:
        return "unknown"
    number = float(value)
    rounded = round(number, 6)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def render_report(findings: list[str], limitations: list[str], attribution: str | None, datasets: list[str]) -> str:
    parts = list(findings)
    if datasets:
        parts.append("Evidence: datasets " + ", ".join(datasets) + ".")
    if attribution:
        parts.append(f"Attribution: {attribution}.")
    if limitations:
        parts.append("Limitations: " + " ".join(limitations))
    return "\n\n".join(parts)
