"""Separate known replay sources from their network delivery protocol."""
from urllib.parse import urlsplit


def is_sentinel_camera(camera_id):
    return str(camera_id).upper().startswith('SENTINEL-')


def source_origin(camera_id, stream_url, local_file=False):
    # Reserved registry IDs also cover the local HLS relay and manual reconnects.
    host = (urlsplit(stream_url).hostname or '').lower()
    if local_file or is_sentinel_camera(camera_id) or host in {
        'cctv.corp8.cloud', '103.250.160.189'
    }:
        return 'RECORDED'
    return 'LIVE'
