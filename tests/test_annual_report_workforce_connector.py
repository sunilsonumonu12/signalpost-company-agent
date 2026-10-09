import importlib.util
from pathlib import Path


CONNECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "archive"
    / "v2-crawler"
    / "run_annual_report_workforce_connector.py"
)
SPEC = importlib.util.spec_from_file_location("annual_report_workforce_connector", CONNECTOR_PATH)
assert SPEC is not None and SPEC.loader is not None
CONNECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONNECTOR)


def test_bauge_eiendom_adjacent_year_values_are_not_merged():
    extracted_text = """
Organisasjonsnr: 810 432 322
BAUGE EIENDOM AS
NOTEOPPLYSNINGER - SELSKAP
Tal på årsverk i rekneskapsåret
1.00
Lønnskostnader 2025 2024
Gjennomsnittlig antall årsverk sysselsatt i regnskapsåret 1 1
"""

    value, _span, status, measure = CONNECTOR.extract_candidate(extracted_text)

    assert (value, status, measure) == (1, "accepted", "full_time_equivalents")


def test_distinct_company_scope_counts_remain_a_conflict():
    extracted_text = """
Tal på årsverk i rekneskapsåret
1.00
Antall årsverk i regnskapsåret 2.00
"""

    value, _span, status, _measure = CONNECTOR.extract_candidate(extracted_text)

    assert value is None
    assert status == "conflicting_employee_counts"