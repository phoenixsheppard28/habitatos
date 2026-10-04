import os
import unittest

import httpx


@unittest.skipUnless(os.environ.get("HABITAT_CLASSIFIER_TEST_URL"), "requires the running GLiClass container")
class ClassifierServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = httpx.Client(base_url=os.environ["HABITAT_CLASSIFIER_TEST_URL"], timeout=60)
        self.addCleanup(self.client.close)

    def test_classifier_distinguishes_movement_and_rainfall(self):
        cases = [
            ("GPS collar observations of seasonal deer migration", "animal movement"),
            ("Daily rainfall observations", "rainfall"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                response = self.client.post("/classify", json={
                    "text": text, "labels": ["animal movement", "rainfall", "vegetation"], "threshold": 0.5,
                })
                response.raise_for_status()
                result = response.json()

                self.assertEqual([item["label"] for item in result["predictions"]], [expected])
                self.assertTrue(0.5 <= result["predictions"][0]["score"] <= 1)
                self.assertEqual(result["model"], "knowledgator/gliclass-small-v1.0")
                self.assertEqual(result["revision"], "21edefaf7951f68c68c505f9139ba536d3b448f7")

    def test_long_text_preserves_labels_and_weak_results_are_omitted(self):
        response = self.client.post("/classify", json={
            "text": "Daily rainfall observations. " * 300,
            "labels": ["animal movement", "rainfall", "vegetation"], "threshold": 0.5,
        })
        response.raise_for_status()
        self.assertEqual(response.json()["predictions"][0]["label"], "rainfall")

        abstained = self.client.post("/classify", json={
            "text": "Daily rainfall observations", "labels": ["forest", "desert", "urban"], "threshold": 1,
        })
        abstained.raise_for_status()
        self.assertEqual(abstained.json()["predictions"], [])

    def test_invalid_requests_are_rejected(self):
        for labels, threshold in [([], 0.5), (["rainfall"], 2), (["<<LABEL>>rainfall"], 0.5)]:
            response = self.client.post("/classify", json={"text": "Rainfall", "labels": labels, "threshold": threshold})
            self.assertEqual(response.status_code, 422)
