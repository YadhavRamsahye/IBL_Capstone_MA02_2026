"""
tests/evidence.py
Shared "test evidence" printer used across the test suite for the manual
testing/QA form.

Every category of test (classification, API, security, database,
performance) prints the same shape of block - a banner, the TC-ID and
description, what was expected vs what was actually observed, and a
PASS/FAIL line - so a screenshot of any single test's console output is
self-explanatory evidence on its own, and so ~200 test methods don't each
reinvent their own print formatting.

Each print_*_evidence() function returns the boolean it printed as PASS/FAIL,
so a test can feed that straight into its own assertion instead of
recomputing the same comparison twice, e.g.:

    ok = print_evidence("TC-001", "...", inputs, expected, actual)
    self.assertTrue(ok)
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

_WIDTH = 60


def _banner(test_id: str, description: str) -> None:
    print("\n" + "=" * _WIDTH)
    print(f"{test_id} - {description}")
    print("=" * _WIDTH)


def _result_line(passed: bool) -> None:
    print(f"Result:   {'PASS' if passed else 'FAIL'}")
    print("=" * _WIDTH)


def print_evidence(test_id: str, description: str, inputs: Any, expected: Any,
                   actual: Any, *, passed: Optional[bool] = None) -> bool:
    """Generic evidence block: one input, one expected value, one actual value.

    `passed` defaults to `expected == actual`; pass it explicitly when the
    pass/fail condition isn't a plain equality (e.g. a tolerance check).
    """
    ok = passed if passed is not None else (expected == actual)
    _banner(test_id, description)
    print(f"Input:    {inputs}")
    print(f"Expected: {expected}")
    print(f"Actual:   {actual}")
    _result_line(ok)
    return ok


def _format_body(response) -> str:
    try:
        return json.dumps(response.json(), indent=4)
    except (ValueError, TypeError):
        text = response.text
        return text if len(text) <= 500 else text[:500] + " …(truncated)"


def print_api_evidence(test_id: str, description: str, *, method: str, path: str,
                       expected_status: int, response,
                       extra: Optional[Mapping[str, Any]] = None) -> bool:
    """Evidence block for one HTTP call made through FastAPI's TestClient."""
    ok = response.status_code == expected_status
    _banner(test_id, description)
    print(f"Request:         {method} {path}")
    print(f"Expected status: {expected_status}")
    print(f"Actual status:   {response.status_code}")
    print(f"Response body:   {_format_body(response)}")
    for label, value in (extra or {}).items():
        print(f"{label}: {value}")
    _result_line(ok)
    return ok


def print_security_evidence(test_id: str, description: str, *, method: str, path: str,
                            auth: str, expected_status: int, actual_status: int,
                            extra: Optional[Mapping[str, Any]] = None) -> bool:
    """Evidence block for an authentication/authorization contract check."""
    ok = expected_status == actual_status
    _banner(test_id, description)
    print(f"Request:        {method} {path}")
    print(f"Authentication: {auth}")
    print(f"Expected:       HTTP {expected_status}")
    print(f"Actual:         HTTP {actual_status}")
    for label, value in (extra or {}).items():
        print(f"{label}: {value}")
    _result_line(ok)
    return ok


def print_db_evidence(test_id: str, description: str, *, context: Mapping[str, Any],
                      expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    """Evidence block for a write -> query -> verify database test.

    `expected` and `actual` should share the same keys; PASS requires every
    field to match, not just some of them.
    """
    ok = all(actual.get(key) == value for key, value in expected.items())
    _banner(test_id, description)
    for label, value in context.items():
        print(f"{label}: {value}")
    for key, value in expected.items():
        print(f"Expected {key}: {value}")
        print(f"Actual   {key}: {actual.get(key)}")
    _result_line(ok)
    return ok


def print_performance_evidence(test_id: str, description: str, *, endpoint: str,
                               total_requests: int, successful_requests: int,
                               response_times: Iterable[float], passed: bool) -> bool:
    """Evidence block for a load/latency measurement.

    Prints only measured values - it never invents a target response time.
    Pass/fail is decided by the caller's own assertions and handed in as
    `passed`, since "acceptable latency" isn't something this helper can know.
    """
    times = sorted(response_times)
    _banner(test_id, description)
    print(f"Endpoint:            {endpoint}")
    print(f"Requests:            {total_requests}")
    print(f"Successful requests: {successful_requests}")
    if times:
        avg = sum(times) / len(times)
        p95_index = min(len(times) - 1, int(round(0.95 * (len(times) - 1))))
        print()
        print(f"Average response time: {avg:.4f} s")
        print(f"Maximum response time: {max(times):.4f} s")
        print(f"P95 response time:     {times[p95_index]:.4f} s")
    print()
    _result_line(passed)
    return passed
