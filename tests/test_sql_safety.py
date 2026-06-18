"""Proves implementation-tasks.md task 6's SQL AST allowlist against the
exact cases found during the task 6 feasibility spike: sqlglot parses
subqueries and multi-statement strings without raising, so "no parse
error" alone proves nothing -- these tests check the actual safety
decision, not just whether parsing succeeded.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from sql_safety import validate_expression  # noqa: E402


def _is_safe(sql: str) -> bool:
    return not validate_expression(sql)


class TestAllowedExpressions(unittest.TestCase):
    def test_simple_arithmetic_over_columns(self):
        self.assertTrue(_is_safe("bs.vol_imb_k1 - bs.vol_imb_k3"))

    def test_case_when(self):
        self.assertTrue(_is_safe("CASE WHEN vs.vol_bucket = 1 THEN bs.vol_imb_k1 ELSE NULL END"))

    def test_allowed_scalar_functions(self):
        self.assertTrue(_is_safe("abs(bs.vol_imb_k1) / sqrt(greatest(vs.realized_vol, 0.0001))"))
        self.assertTrue(_is_safe("least(a, b) + coalesce(c, 0) + nullif(d, 0) + sign(e)"))
        self.assertTrue(_is_safe("clip(a, 0, 1)"))  # no dedicated sqlglot node -- surfaces as Anonymous

    def test_bounded_preceding_window_with_aggregate(self):
        self.assertTrue(_is_safe("avg(x) OVER (ORDER BY ts ROWS BETWEEN 10 PRECEDING AND CURRENT ROW)"))
        self.assertTrue(_is_safe("min(x) OVER (ORDER BY ts ROWS BETWEEN 5 PRECEDING AND CURRENT ROW)"))

    def test_bounded_range_window_is_also_allowed(self):
        # The contract allows ROWS "or an equivalent time-safe preceding-only frame".
        self.assertTrue(_is_safe("avg(x) OVER (ORDER BY ts RANGE BETWEEN 30 PRECEDING AND CURRENT ROW)"))

    def test_count_star_in_window(self):
        self.assertTrue(_is_safe("count(*) OVER (ORDER BY ts ROWS BETWEEN 10 PRECEDING AND CURRENT ROW)"))

    def test_sql_comment_is_harmless(self):
        self.assertTrue(_is_safe("vol_imb_k1 -- comment injection\n"))


class TestRejectedExpressions(unittest.TestCase):
    def test_subquery_rejected_despite_parsing_cleanly(self):
        # Confirmed during the spike: sqlglot.parse_one does NOT raise for this.
        errors = validate_expression("(SELECT avg(price) FROM trade)")
        self.assertTrue(errors)
        self.assertTrue(any("Subquery" in e for e in errors))

    def test_multi_statement_injection_rejected(self):
        # Confirmed during the spike: parses into a Block containing a Drop,
        # no parse error -- must be caught structurally, not by exception.
        errors = validate_expression("1; DROP TABLE trade;")
        self.assertTrue(errors)
        self.assertTrue(any("single statement" in e for e in errors))

    def test_select_join_rejected(self):
        errors = validate_expression("SELECT a FROM b JOIN c ON b.id = c.id")
        self.assertTrue(any("Select" in e or "Join" in e for e in errors))

    def test_file_function_rejected(self):
        self.assertFalse(_is_safe("read_csv('/etc/passwd')"))

    def test_unallowlisted_anonymous_function_rejected(self):
        # now() has no dedicated sqlglot node type -- surfaces as generic
        # Anonymous, same shape as any allowed function; must be rejected
        # by name, not by type.
        errors = validate_expression("now() - ts")
        self.assertTrue(any("now" in e for e in errors))

    def test_unbounded_window_with_no_frame_rejected(self):
        errors = validate_expression("avg(bs.vol_imb_k1) OVER (ORDER BY ts)")
        self.assertTrue(any("no explicit frame" in e for e in errors))

    def test_window_with_following_rejected(self):
        errors = validate_expression("avg(x) OVER (ORDER BY ts ROWS BETWEEN 10 PRECEDING AND 5 FOLLOWING)")
        self.assertTrue(any("CURRENT ROW" in e for e in errors))

    def test_window_with_unbounded_preceding_rejected(self):
        errors = validate_expression("avg(x) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)")
        self.assertTrue(any("UNBOUNDED" in e for e in errors))

    def test_scalar_function_used_as_window_function_rejected(self):
        # sqrt is allowed as a scalar function, but not meaningful (or
        # allowed) as the aggregate being rolled over a window frame.
        errors = validate_expression("sqrt(x) OVER (ORDER BY ts ROWS BETWEEN 5 PRECEDING AND CURRENT ROW)")
        self.assertTrue(any("window function 'sqrt'" in e for e in errors))

    def test_garbage_sql_rejected(self):
        self.assertFalse(_is_safe("garbage ((( not sql"))

    def test_empty_expression_rejected(self):
        self.assertFalse(_is_safe(""))
        self.assertFalse(_is_safe("   "))


if __name__ == "__main__":
    unittest.main()
