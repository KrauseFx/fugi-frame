import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig, load_config
from app.indexer import (
    ImmichSource,
    _matches_immich_orientation_filter,
    _matches_immich_people_filter,
)


class ImmichPeopleFilterTests(unittest.TestCase):
    def test_config_loads_orientation_filter_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps({"orientation_allowlist": ["landscape"]}),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertEqual(config.orientation_allowlist, ["landscape"])

    def test_config_loads_people_filter_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "immich_person_allowlist": ["person-sophie", "Felix"],
                        "immich_person_match_mode": "all",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertEqual(config.immich_person_allowlist, ["person-sophie", "Felix"])
        self.assertEqual(config.immich_person_match_mode, "all")

    def test_people_filter_any_mode_matches_id_or_name(self):
        asset = {
            "people": [
                {"id": "person-sophie", "name": "Sophie", "isHidden": False},
                {"id": "person-other", "name": "Someone Else", "isHidden": False},
            ]
        }

        self.assertTrue(_matches_immich_people_filter(asset, ["person-sophie", "person-felix"], "any"))
        self.assertTrue(_matches_immich_people_filter(asset, ["sophie"], "any"))
        self.assertFalse(_matches_immich_people_filter(asset, ["person-felix"], "any"))

    def test_people_filter_all_mode_requires_every_allowed_person(self):
        asset = {
            "people": [
                {"id": "person-sophie", "name": "Sophie", "isHidden": False},
                {"id": "person-felix", "name": "Felix", "isHidden": False},
            ]
        }

        self.assertTrue(_matches_immich_people_filter(asset, ["person-sophie", "person-felix"], "all"))
        self.assertFalse(_matches_immich_people_filter(asset, ["person-sophie", "person-missing"], "all"))

    def test_people_filter_ignores_hidden_people(self):
        asset = {
            "people": [
                {"id": "person-hidden", "name": "Hidden", "isHidden": True},
                {"id": "person-visible", "name": "Visible", "isHidden": False},
            ]
        }

        self.assertFalse(_matches_immich_people_filter(asset, ["person-hidden"], "any"))
        self.assertTrue(_matches_immich_people_filter(asset, ["person-visible"], "any"))

    def test_orientation_filter_uses_exif_rotation(self):
        raw_landscape_rotated_portrait = {
            "exifInfo": {"exifImageWidth": 6240, "exifImageHeight": 4160, "orientation": "8"}
        }
        raw_landscape_unrotated = {
            "exifInfo": {"exifImageWidth": 6240, "exifImageHeight": 4160, "orientation": "1"}
        }

        self.assertFalse(
            _matches_immich_orientation_filter(raw_landscape_rotated_portrait, ["landscape"])
        )
        self.assertTrue(
            _matches_immich_orientation_filter(raw_landscape_rotated_portrait, ["portrait"])
        )
        self.assertTrue(_matches_immich_orientation_filter(raw_landscape_unrotated, ["landscape"]))

    def test_immich_source_filters_by_effective_orientation(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "assets": {
                        "items": [
                            {
                                "id": "asset-landscape",
                                "fileCreatedAt": "2026-01-01T12:00:00.000Z",
                                "originalPath": "/data/library/admin/FUJIFILM/X-T5/file.jpg",
                                "visibility": "timeline",
                                "exifInfo": {
                                    "exifImageWidth": 6240,
                                    "exifImageHeight": 4160,
                                    "orientation": "1",
                                },
                            },
                            {
                                "id": "asset-rotated-portrait",
                                "fileCreatedAt": "2026-01-01T13:00:00.000Z",
                                "originalPath": "/data/library/admin/FUJIFILM/X-T5/file.jpg",
                                "visibility": "timeline",
                                "exifInfo": {
                                    "exifImageWidth": 6240,
                                    "exifImageHeight": 4160,
                                    "orientation": "6",
                                },
                            },
                        ]
                    }
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def post(self, url, headers, json):
                return FakeResponse()

        config = AppConfig(
            source="immich",
            immich_url="http://immich.example",
            immich_api_key="dummy-key",
            camera_make_allowlist=[],
            orientation_allowlist=["landscape"],
        )

        with patch("app.indexer.httpx.Client", FakeClient):
            records = ImmichSource(config, logging.getLogger("test")).fetch_records()

        self.assertEqual([record.uuid for record in records], ["asset-landscape"])

    def test_immich_source_requests_people_metadata_and_filters_results(self):
        posted_bodies = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "assets": {
                        "items": [
                            {
                                "id": "asset-match",
                                "fileCreatedAt": "2026-01-01T12:00:00.000Z",
                                "originalPath": "/data/library/admin/FUJIFILM/X-T5/file.jpg",
                                "visibility": "timeline",
                                "people": [{"id": "person-sophie", "name": "Sophie"}],
                            },
                            {
                                "id": "asset-skip",
                                "fileCreatedAt": "2026-01-01T13:00:00.000Z",
                                "originalPath": "/data/library/admin/FUJIFILM/X-T5/file.jpg",
                                "visibility": "timeline",
                                "people": [{"id": "person-other", "name": "Other"}],
                            },
                        ]
                    }
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def post(self, url, headers, json):
                posted_bodies.append(json)
                return FakeResponse()

        config = AppConfig(
            source="immich",
            immich_url="http://immich.example",
            immich_api_key="dummy-key",
            camera_make_allowlist=[],
            immich_person_allowlist=["person-sophie"],
        )

        with patch("app.indexer.httpx.Client", FakeClient):
            records = ImmichSource(config, logging.getLogger("test")).fetch_records()

        self.assertEqual([record.uuid for record in records], ["asset-match"])
        self.assertEqual(records[0].people, ("person-sophie",))
        self.assertEqual(posted_bodies[0]["withPeople"], True)


if __name__ == "__main__":
    unittest.main()
