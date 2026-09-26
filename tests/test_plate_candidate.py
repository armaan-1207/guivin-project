import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from evaluate_plate_candidate import iou, match_predictions, load_images, vehicle_regions, suppress_duplicate_boxes


class PlateCandidateTests(unittest.TestCase):
    def test_vehicle_crops_pad_clamp_and_do_not_invent_a_region_on_miss(self):
        self.assertEqual(vehicle_regions([[2,3,90,95]],100,100),[(0,3,95,100)])
        self.assertEqual(vehicle_regions([],100,100),[])
        self.assertEqual(vehicle_regions([[30,30,10,10]],100,100),[])
        with self.assertRaises(ValueError):
            vehicle_regions([[0,0,float('nan'),20]],100,100)

    def test_overlap_between_vehicle_crops_does_not_duplicate_ocr_work(self):
        low = {'box':[10,10,30,20],'detection_confidence':.5}
        high = {'box':[10,10,30,20],'detection_confidence':.9}
        other = {'box':[50,50,80,60],'detection_confidence':.6}
        self.assertEqual(suppress_duplicate_boxes([low,other,high]),[high,other])

    def test_duplicate_detection_matches_only_once_in_detector_confidence_order(self):
        truth = [{'box':[10,10,30,20]}]
        predictions = [
            {'box':[10,10,30,20],'detection_confidence':.6,'confidence':.99},
            {'box':[10,10,30,20],'detection_confidence':.9,'confidence':.3}]
        self.assertEqual(match_predictions(predictions,truth), [(1,0)])
        self.assertEqual(len(predictions)-len(match_predictions(predictions,truth)),1)

    def test_no_overlap_and_localization_miss(self):
        self.assertEqual(iou([0,0,10,10],[10,10,20,20]),0)
        self.assertEqual(iou([0,0,10,10],[0,0,10,10]),1)
        self.assertEqual(match_predictions([{'box':[0,0,10,10],'detection_confidence':.99}],
                                          [{'box':[9,9,20,20]}]),[])
        self.assertEqual(match_predictions([], [{'box':[0,0,10,10]}]),[])

    def test_all_boxes_count_even_without_text_and_only_downloaded_images_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'images').mkdir()
            (root/'Annotations').mkdir()
            (root/'images'/'one.jpg').write_bytes(b'image')
            xml='''<annotation><object><name>number_plate</name><bndbox>
            <xmin>1</xmin><ymin>2</ymin><xmax>20</xmax><ymax>10</ymax>
            </bndbox></object></annotation>'''
            (root/'Annotations'/'one.xml').write_text(xml)
            (root/'Annotations'/'not-downloaded.xml').write_text(xml)
            rows=load_images(root)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][2],[{'box':[1,2,20,10],'text':''}])
            (root/'Annotations'/'one.xml').write_text(xml.replace('<xmax>20','<xmax>0'))
            with self.assertRaises(ValueError):
                load_images(root)

    def test_empty_annotations_are_not_assumed_negatives(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'images').mkdir()
            (root/'Annotations').mkdir()
            (root/'images'/'one.jpg').write_bytes(b'image')
            (root/'Annotations'/'one.xml').write_text('<annotation/>')
            with self.assertRaises(ValueError):
                load_images(root)
