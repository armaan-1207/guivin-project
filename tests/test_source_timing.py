import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.source_timing import SourceTimeline

class SourceTimingTests(unittest.TestCase):
    def test_pts_intervals_ignore_delivery_bursts_and_reset(self):
        clock=SourceTimeline()
        received=datetime(2026,9,19)
        self.assertEqual(clock.read(1000,received)['delta'],0)
        burst=clock.read(2000,received+timedelta(milliseconds=1))
        self.assertEqual(burst['delta'],1)
        self.assertEqual(burst['utc'],received+timedelta(seconds=1))
        self.assertIsNone(clock.read(2000,received))
        reset=clock.read(0,received+timedelta(seconds=5))
        self.assertTrue(reset['reset'])
        self.assertEqual(reset['delta'],0)
        for value in (None,float('nan'),-1,float('inf')):
            self.assertIsNone(clock.read(value,received))
