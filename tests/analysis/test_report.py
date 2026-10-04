from analysis.report import fmt, render_report


def test_numbers_format_without_pretending_null_is_zero():
    assert fmt(None) == "unknown"
    assert fmt(4.0) == "4"
    assert fmt(1.5) == "1.5"
    assert fmt(1.2500004) == "1.25"


def test_report_keeps_evidence_and_limitations_beside_findings():
    text = render_report(
        ["Median daily displacement was 4 km."],
        ["Findings describe the tracked animals in this sample, not a wildlife census."],
        "Demo fixture",
        ["dataset-movement@1"],
    )
    assert text.index("Median daily displacement") < text.index("Evidence:")
    assert "dataset-movement@1" in text
    assert "Attribution: Demo fixture." in text
    assert "not a wildlife census" in text
    assert "0 km" not in text
