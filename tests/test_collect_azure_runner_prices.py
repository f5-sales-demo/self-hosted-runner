from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "collect_azure_runner_prices", ROOT / "scripts/collect-azure-runner-prices.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AzureRunnerPriceTests(unittest.TestCase):
    def test_collect_pages_checkpoints_and_filters_exact_runner_meters(self) -> None:
        pages = {
            "first": {
                "properties": {
                    "pricesheets": [
                        {
                            "meterDetails": {
                                "meterName": "D16ads v5",
                                "meterSubCategory": "Dadsv5 Series",
                                "meterLocation": "CA Central",
                            },
                            "unitOfMeasure": "1 Hour",
                            "unitPrice": 0.7,
                            "currencyCode": "USD",
                        },
                        {"meterDetails": {"meterName": "Unrelated"}},
                    ],
                    "nextLink": "second",
                }
            },
            "second": {
                "properties": {
                    "pricesheets": [
                        {
                            "properties": {
                                "meterDetails": {
                                    "meterName": "F32s v2",
                                    "meterSubCategory": "FSv2 Series",
                                    "meterLocation": "CA Central",
                                },
                                "unitOfMeasure": "1 Hour",
                                "unitPrice": 1.1,
                                "currencyCode": "USD",
                            }
                        }
                    ],
                    "nextLink": None,
                }
            },
        }
        state = {
            "next_url": "first",
            "pages": 0,
            "entries": 0,
            "matches": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            result = MODULE.collect_pages(state, checkpoint, pages.__getitem__)
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertEqual(2, result["pages"])
        self.assertEqual(3, result["entries"])
        self.assertEqual(
            ["D16ads v5", "F32s v2"], [item["meterName"] for item in result["matches"]]
        )
        self.assertIsNone(saved["next_url"])

    def test_normalize_runner_price_rejects_unrelated_and_invalid_entries(self) -> None:
        self.assertIsNone(MODULE.normalize_runner_price(None))
        self.assertIsNone(MODULE.normalize_runner_price({"meterDetails": {}}))
        self.assertIsNone(
            MODULE.normalize_runner_price(
                {"meterDetails": {"meterName": "Standard Fixed Cost"}}
            )
        )


if __name__ == "__main__":
    unittest.main()
