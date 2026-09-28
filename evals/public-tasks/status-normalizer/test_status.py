import unittest

from status import is_valid_status, normalize_status


class StatusNormalizerTests(unittest.TestCase):
    def test_canonical_value_is_unchanged(self) -> None:
        self.assertEqual(normalize_status("active"), "active")

    def test_whitespace_and_case_are_normalized(self) -> None:
        self.assertEqual(normalize_status(" Active "), "active")

    def test_unknown_status_is_rejected(self) -> None:
        self.assertFalse(is_valid_status("archived"))


if __name__ == "__main__":
    unittest.main()
