import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.plate_text import assemble_plate


def part(text, x=0, y=0, width=40, confidence=.9):
    return ([[x,y],[x+width,y],[x+width,y+20],[x,y+20]], text, confidence)


class PlateTextTests(unittest.TestCase):
    def test_fragments_are_sorted_and_joined_across_two_lines(self):
        result = assemble_plate([part('1234', 50, 25, confidence=.7),
                                 part('GJ01', 0, 0), part('AB', 0, 25)])
        self.assertEqual(result, ('GJ01AB1234', .7))

    def test_single_line_fragments_and_ind_mark(self):
        self.assertEqual(assemble_plate([part('1234',100), part('IND',0,30),
                                        part('AB',50), part('GJ01',0)])[0], 'GJ01AB1234')

    def test_low_confidence_character_is_not_guessed(self):
        self.assertEqual(assemble_plate([part('GJ01'),part('AB',50,confidence=.1),part('1234',100)]), ('',0))

    def test_far_apart_signage_is_not_joined(self):
        for entries in [[part('GJ01'),part('AB',500),part('1234',550)],
                        [part('GJ01'),part('AB',0,200),part('1234',50,200)]]:
            self.assertEqual(assemble_plate(entries), ('',0))

    def test_bharat_format_and_empty_input(self):
        self.assertEqual(assemble_plate([part('22BH',0),part('1234AA',50)])[0], '22BH1234AA')
        self.assertEqual(assemble_plate([]), ('',0))

    def test_fallback_rejects_signage_that_only_has_digits(self):
        self.assertEqual(assemble_plate([part('PARKING123', confidence=.99)]), ('',0))

    def test_fallback_accepts_a_complete_registration(self):
        self.assertEqual(assemble_plate([part('GJ01AB1234')]), ('GJ01AB1234', .9))
