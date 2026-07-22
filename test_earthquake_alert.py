import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

import earthquake_alert


class TmdPublicationWindowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    def is_recent(self, event):
        self.assertTrue(
            hasattr(earthquake_alert, "is_recent_tmd_publication"),
            "TMD pubDate window filter is not implemented",
        )
        return earthquake_alert.is_recent_tmd_publication(event, self.now)

    def test_accepts_pubdate_within_15_minutes(self):
        event = {"available_at": self.now - timedelta(minutes=15)}
        self.assertTrue(self.is_recent(event))

    def test_rejects_pubdate_older_than_15_minutes(self):
        event = {"available_at": self.now - timedelta(minutes=15, seconds=1)}
        self.assertFalse(self.is_recent(event))

    def test_rejects_missing_or_invalid_pubdate(self):
        self.assertFalse(self.is_recent({"available_at": None}))

    def test_rejects_future_pubdate(self):
        event = {"available_at": self.now + timedelta(seconds=1)}
        self.assertFalse(self.is_recent(event))

    @patch("earthquake_alert.requests.get")
    def test_missing_pubdate_is_not_replaced_with_event_time(self, mock_get):
        response = Mock()
        response.content = b"""<?xml version="1.0"?>
        <rss xmlns:geo="http://www.w3.org/2003/01/geo/"
             xmlns:tmd="http://www.earthquake.tmd.go.th">
          <channel><item>
            <title>Test Region</title>
            <link>https://earthquake.tmd.go.th/inside-info.html?earthquake=123</link>
            <tmd:time>2026-07-22 11:55:00 UTC</tmd:time>
            <tmd:magnitude>5.2</tmd:magnitude>
            <tmd:depth>10</tmd:depth>
            <geo:lat>13.0</geo:lat><geo:long>100.0</geo:long>
          </item></channel>
        </rss>"""
        mock_get.return_value = response

        events = earthquake_alert.fetch_tmd_earthquakes()

        self.assertIsNone(events[0]["available_at"])


if __name__ == "__main__":
    unittest.main()
