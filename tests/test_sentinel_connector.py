import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from urllib.parse import urlsplit,unquote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.sentinel_connector import SentinelConnector, parse_catalogue

class SentinelConnectorTests(unittest.TestCase):
    def test_failed_refresh_clears_cached_catalogue(self):
        import requests
        connector = SentinelConnector('fixture@example.invalid', 'fixture')
        for response, error, expected in [
            (Mock(status_code=503), None, 'catalogue'),
            (Mock(status_code=401), None, 'authentication'),
            (None, requests.ConnectionError('unreachable'), 'network'),
            (Mock(status_code=200, json=Mock(return_value=[{'id': '../invalid'}])), None, 'catalogue'),
        ]:
            with self.subTest(expected=expected):
                connector._logged_in = True
                connector._cameras = [{'id': 'old-camera'}]
                with patch.object(connector._session, 'get', return_value=response, side_effect=error):
                    self.assertEqual(connector.fetch_cameras(), [])
                self.assertEqual(connector._cameras, [])
                self.assertEqual(connector.last_error, expected)
                if expected == 'authentication':
                    self.assertFalse(connector._logged_in)

    def test_network_failure_is_distinct_from_bad_credentials(self):
        import requests
        connector = SentinelConnector('fixture@example.invalid', 'fixture')
        with patch.object(connector._session, 'post', side_effect=requests.ConnectionError('unreachable')):
            self.assertFalse(connector.login())
        self.assertEqual(connector.last_error, 'network')

    def test_catalogue_rejects_bad_shapes_ids_duplicates_and_coordinates(self):
        invalid = [None, {}, {'cameras': {}}, [None], [{'name': 'No ID'}],
                   [{'id': '../auth/logout'}], [{'id': 'cam01?token=x'}],
                   [{'id': 'cam01'}, {'id': 'CAM01'}],
                   [{'id': 'cam01', 'lat': 'NaN'}],
                   [{'id': 'cam01', 'lon': 181}],
                   [{'id': 'cam01', 'lat': True}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_catalogue(value)

    def test_catalogue_preserves_zero_and_missing_coordinates(self):
        cameras = parse_catalogue({'cameras': [
            {'id': 'cam01', 'name': 'Fixture', 'lat': 0, 'lon': 0},
            {'camera_id': 'cam02', 'latitude': '23.5', 'longitude': '72.5'},
            {'id': 'cam03'}]})
        self.assertEqual((cameras[0]['lat'], cameras[0]['lon']), (0, 0))
        self.assertEqual(cameras[1]['lat'], 23.5)
        self.assertIsNone(cameras[2]['lat'])

    def test_login_rejects_html_failure_and_external_redirect(self):
        connector = SentinelConnector('fixture@example.invalid', 'fixture')
        for status, location, success in [(200, '', False), (302, '/auth/login', False),
                (302, 'https://external.invalid/', False), (303, '/', True)]:
            connector._logged_in = True
            response = Mock(status_code=status, headers={'Location': location})
            with patch.object(connector._session, 'post', return_value=response) as post:
                self.assertEqual(connector.login(), success)
                self.assertEqual(connector._logged_in, success)
                self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_url_builders_reject_path_injection(self):
        connector = SentinelConnector('fixture@example.invalid', 'fixture')
        for build in [connector.hls_url, connector.rtsp_url, connector.webrtc_url]:
            with self.assertRaises(ValueError):
                build('../auth/logout')

    def test_credential_encoding_and_error_redaction(self):
        connector=SentinelConnector('test@example.invalid','test:@/secret')
        parsed=urlsplit(connector.rtsp_url('fixture'))
        self.assertEqual(unquote(parsed.username),'test@example.invalid')
        self.assertEqual(unquote(parsed.password),'test:@/secret')
        with patch.object(connector._session,'post',side_effect=RuntimeError('test:@/secret')):
            with self.assertLogs('guivin.sentinel',level='ERROR') as logs:
                self.assertFalse(connector.login())
        self.assertNotIn('test:@/secret',' '.join(logs.output))
