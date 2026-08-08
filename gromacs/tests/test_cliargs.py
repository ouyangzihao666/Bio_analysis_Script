"""Option-level merging tests."""

from __future__ import annotations

import unittest

from mdkit.cliargs import merge_cli_options, parse_options, slot_missing_ntmpi


class CliArgsTests(unittest.TestCase):
    def test_parse_options(self):
        self.assertEqual(
            parse_options(["-deffnm", "x", "-v", "-ntomp", "32"]),
            [("-deffnm", "x"), ("-v", None), ("-ntomp", "32")],
        )

    def test_merge_dedups_last_wins(self):
        merged = merge_cli_options(
            ["mdrun", "-deffnm", "md", "-gpu_id", "0", "-v"],
            ["-gpu_id", "1", "-pinoffset", "64"],
        )
        self.assertEqual(
            merged,
            ["mdrun", "-deffnm", "md", "-gpu_id", "1", "-v", "-pinoffset", "64"],
        )
        self.assertEqual(merged.count("-gpu_id"), 1)

    def test_merge_flag_dedup(self):
        merged = merge_cli_options(["-v"], ["-v"])
        self.assertEqual(merged, ["-v"])

    def test_merge_three_layers(self):
        merged = merge_cli_options(
            ["-ntomp", "8"],
            ["-ntmpi", "1", "-ntomp", "16"],
            ["-ntomp", "32", "-gpu_id", "0"],
        )
        self.assertEqual(
            merged,
            ["-ntomp", "32", "-ntmpi", "1", "-gpu_id", "0"],
        )

    def test_slot_gpu_ntomp_requires_ntmpi(self):
        self.assertIsNone(slot_missing_ntmpi(""))
        self.assertIsNone(slot_missing_ntmpi("-ntomp 32"))
        self.assertIsNone(slot_missing_ntmpi("-ntmpi 1 -ntomp 32 -gpu_id 1"))
        self.assertIsNone(slot_missing_ntmpi("-ntomp 16 -nb cpu"))
        self.assertIsNotNone(slot_missing_ntmpi("-ntomp 32 -gpu_id 0"))
        problem = slot_missing_ntmpi("-ntomp 32 -gpu_id 1 -pinoffset 64")
        self.assertIsNotNone(problem)
        self.assertIn("-ntmpi", problem)
        problem2 = slot_missing_ntmpi("-ntomp 16 -nb gpu")
        self.assertIsNotNone(problem2)


if __name__ == "__main__":
    unittest.main()
