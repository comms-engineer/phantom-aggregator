import json
import unittest
from pathlib import Path

from app.fetchers.fema_alerts import FemaAlertsFetcher
from app.fetchers.space_weather import SpaceWeatherFetcher
from app.renderers.nomadnet import NomadNetRenderer


class SourceFixesTests(unittest.TestCase):
    def test_sources_config_uses_updated_endpoints(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "sources.json"
        document = json.loads(config_path.read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in document["sources"]}

        self.assertIn("wildfires_active", sources)
        self.assertEqual(
            sources["wildfires_active"]["url"],
            "https://inciweb.wildfire.gov/incidents/rss.xml",
        )
        self.assertEqual(
            sources["noaa_space_weather"]["options"]["forecast_url"],
            "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json",
        )
        self.assertEqual(
            sources["noaa_space_weather"]["options"]["solar_flux_url"],
            "https://services.swpc.noaa.gov/products/10cm-flux-30-day.json",
        )
        self.assertIn("siteType=ST", sources["flooding_water"]["options"]["usgs_gauge_url"])
        self.assertIn("IpawsArchivedAlerts", sources["fema_alerts"]["url"])

    def test_nomadnet_index_uses_working_link_markers(self):
        rendered = NomadNetRenderer().render_index([
            ("emergencies.mu", "Emergencies"),
            ("space.mu", "Space Weather"),
        ])

        self.assertIn(
            "`_`[Emergencies`:/page/phantom-aggregator/emergencies.mu]`_`",
            rendered,
        )

    def test_space_weather_table_to_records_handles_legacy_and_modern_schema(self):
        fetcher = SpaceWeatherFetcher(
            k_index_url="https://example.test/k.json",
            solar_flux_url="https://example.test/f.json",
            forecast_url="https://example.test/forecast.json",
        )

        legacy = [["time_tag", "kp"], ["2026-09-01T00:00:00", 5]]
        modern = [{"time_tag": "2026-09-01T00:00:00", "kp": 5}]

        self.assertEqual(fetcher._table_to_records(legacy), [{"time_tag": "2026-09-01T00:00:00", "kp": 5}])
        self.assertEqual(fetcher._table_to_records(modern), modern)

    def test_space_weather_metrics_use_live_field_names(self):
        fetcher = SpaceWeatherFetcher(
            k_index_url="https://example.test/k.json",
            solar_flux_url="https://example.test/f.json",
            forecast_url="https://example.test/forecast.json",
        )

        k_index_body = [
            {"time_tag": "2026-09-01T00:00:00", "Kp": 5},
            {"time_tag": "2026-09-02T00:00:00", "Kp": 7},
        ]
        solar_flux_body = [{"time_tag": "2026-09-02T00:00:00", "flux": 120}]
        forecast_body = [
            {"time_tag": "2026-09-02T00:00:00", "kp": 6, "observed": "predicted", "noaa_scale": "G2"},
            {"time_tag": "2026-09-03T00:00:00", "kp": 4, "observed": "predicted", "noaa_scale": "G1"},
        ]

        normalized = fetcher._normalize_metrics(k_index_body, solar_flux_body, forecast_body)
        self.assertEqual(normalized["k_index"], 7)
        self.assertEqual(normalized["sfi"], 120)
        self.assertEqual(normalized["geomagnetic_storm"], "G2")

    def test_fema_alerts_parse_ipaws_alerts_handles_multiple_info_blocks(self):
        fetcher = FemaAlertsFetcher(url="https://www.fema.gov/api/open/v1/IpawsArchivedAlerts?$top=10", options={})
        body = {
            "IpawsArchivedAlerts": [
                {
                    "sent": "2024-03-18T12:00:00Z",
                    "identifier": "abc-1",
                    "info": [
                        {
                            "headline": "Evacuation Warning",
                            "description": "Mandatory evacuation for area 1.",
                            "event": "Flash Flood Warning",
                            "area": [{"areaDesc": "County A"}],
                        },
                        {
                            "headline": "Second Notice",
                            "description": "Additional details.",
                            "event": "Flash Flood Warning",
                        },
                    ],
                },
                {
                    "sent": "2024-03-17T09:00:00Z",
                    "identifier": "abc-2",
                    "info": [{"headline": "No Area", "description": "Body text"}],
                },
            ]
        }

        items = fetcher._parse_ipaws_alerts(body)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Evacuation Warning (County A)")
        self.assertIn("evacuation", items[0]["body"].lower())
        self.assertEqual(items[0]["severity"], "critical")
        self.assertEqual(items[1]["title"], "No Area")

    def test_wildfires_parser_handles_inciweb_rss(self):
        from app.fetchers.wildfires import WildfiresFetcher

        xml = '''
        <rss version="2.0">
          <channel>
            <item>
              <title>WAOWF Sisi Fire</title>
              <link>http://inciweb.wildfire.gov/incident-information/waowf-sisi-fire</link>
              <description>Last updated: 2026-09-09

The type of incident is Wildfire and involves the following unit(s) Okanogan-Wenatchee National Forest.

State: Washington

Coordinates:
Latitude: 48° 20 48  Longitude: 120° 49 50

Incident Overview: The Sisi Fire is located in the woods.</description>
              <pubDate>Wed, 09 Sep 2026 00:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        '''

        fetcher = WildfiresFetcher(source_url="https://inciweb.wildfire.gov/incidents/rss.xml")
        items = fetcher._parse_feed(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "WAOWF Sisi Fire")
        self.assertEqual(items[0]["event_type"], "wildfire")
        self.assertIn("Washington", items[0]["body"])

    def test_wildfires_correlation_merges_hotspots_with_incidents(self):
        from app.fetchers.wildfires import WildfiresFetcher

        fetcher = WildfiresFetcher(source_url="https://inciweb.wildfire.gov/incidents/rss.xml")
        incident = {
            "title": "WAOWF Sisi Fire",
            "published": "2026-09-09T00:00:00Z",
            "source": "inciweb.wildfire.gov",
            "link": "https://example.test/fire",
            "body": "State: Washington",
            "fire_name": "Sisi Fire",
            "event_type": "wildfire",
            "severity": "high",
            "severity_rank": 5,
            "incident_group": "wildfire",
        }
        firms = [
            {
                "title": "NASA FIRMS active fire detections (MODIS)",
                "published": "2026-09-09T01:00:00Z",
                "source": "NASA FIRMS",
                "link": "https://example.test/modis",
                "body": "8 wildfire hotspot features were detected in the last 24 hours by MODIS.",
                "fire_name": "MODIS",
                "event_type": "wildfire_hotspot",
                "severity": "medium",
                "severity_rank": 4,
                "incident_group": "wildfire",
                "firms_hotspot_count": 8,
                "firms_sensor": "MODIS",
            },
            {
                "title": "NASA FIRMS active fire detections (VIIRS)",
                "published": "2026-09-09T02:00:00Z",
                "source": "NASA FIRMS",
                "link": "https://example.test/viirs",
                "body": "12 wildfire hotspot features were detected in the last 24 hours by VIIRS.",
                "fire_name": "VIIRS",
                "event_type": "wildfire_hotspot",
                "severity": "high",
                "severity_rank": 5,
                "incident_group": "wildfire",
                "firms_hotspot_count": 12,
                "firms_sensor": "VIIRS",
            },
        ]

        correlated = fetcher._correlate_and_dedupe([incident, *firms])
        self.assertEqual(len(correlated), 1)
        self.assertEqual(correlated[0]["title"], "WAOWF Sisi Fire")
        self.assertEqual(correlated[0]["firms_hotspot_count"], 20)
        self.assertEqual(correlated[0]["severity_rank"], 5)


if __name__ == "__main__":
    unittest.main()
