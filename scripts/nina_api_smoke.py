"""Smoke test for NINA Bridge and Mock NINA."""

import time
import unittest
from unittest.mock import MagicMock

from app.services.nina_client import NinaBridgeService


class TestNinaSmoke(unittest.TestCase):
    def setUp(self):
        # Assumes mock_nina is running in docker network at mock-nina:1888
        # Or localhost:1888 if running locally.
        # We'll default to the docker network name since we are running in container.
        import os
        base_url = os.getenv("NINA_API_URL", "http://mock-nina:1888/api")
        self.service = NinaBridgeService(base_url)

    def test_01_connect_mount(self):
        print("\nTesting Mount Connection...")
        response = self.service.connect_telescope(True)
        print(f"Connect Response: {response}")
        self.assertEqual(response, "Connected")
        
        # Verify status
        status = self.service.get_status()
        self.assertTrue(status["telescope"]["is_connected"])

    def test_02_slew_mount(self):
        print("\nTesting Mount Slew...")
        # Ensure unparked
        self.service.park_telescope(False)
        
        ra = 10.5
        dec = 45.0
        response = self.service.slew(ra, dec)
        print(f"Slew Response: {response}")
        self.assertEqual(response, "Slew finished")
        
        status = self.service.get_status()
        self.assertAlmostEqual(status["telescope"]["ra_deg"], ra)
        self.assertAlmostEqual(status["telescope"]["dec_deg"], dec)

    def test_03_camera_exposure(self):
        print("\nTesting Camera Exposure...")
        # Start exposure
        response = self.service.start_exposure("L", 1, 1.0)
        print(f"Exposure Start Response: {response}")
        self.assertEqual(response, "Capture started")
        
        # Wait for completion
        time.sleep(1.5)
        status = self.service.get_status()
        self.assertEqual(status["camera"]["last_status"], "complete")

    def test_04_focuser_move(self):
        print("\nTesting Focuser Move...")
        pos = 5000
        response = self.service.focuser_move(pos)
        print(f"Focuser Move Response: {response}")
        self.assertEqual(response, "Move started")
        
        time.sleep(0.2)
        status = self.service.get_status()
        self.assertEqual(status["focuser"]["position"], pos)

if __name__ == "__main__":
    unittest.main()
