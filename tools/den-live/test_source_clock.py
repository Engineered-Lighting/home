"""Browser forecast clocks are available without the optional lighting service."""
import http.client
import json
import unittest

from stream import PeopleStream


class SourceClockTest(unittest.TestCase):
    def test_people_connection_bootstraps_producer_clock_without_people_or_spots(self):
        stream = PeopleStream(port=0, clock=lambda: 123.25, body_clock=lambda: 900000.0)
        stream.start()
        connection = http.client.HTTPConnection("127.0.0.1", stream.port, timeout=2)
        try:
            connection.request("GET", "/api/den-live/v1/homes/den/stream")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            name, data = None, None
            for _ in range(8):
                line = response.readline().decode().strip()
                if line.startswith("event: "):
                    name = line[7:]
                if line.startswith("data: "):
                    data = json.loads(line[6:]); break
            self.assertEqual(name, "source_clock")
            self.assertEqual(data, {"schema": "den-source-clock-1", "clock_domain": "producer_monotonic",
                                    "server_mono": 123.25})
            self.assertIsNone(stream.spot_latest)
            self.assertIsNone(stream.latest)
        finally:
            connection.close()
            stream.close()


if __name__ == "__main__":
    unittest.main()
