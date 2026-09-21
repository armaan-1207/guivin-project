"""Media PTS drives intervals; UTC is explicitly a session anchor estimate."""
import math
from datetime import timedelta

class SourceTimeline:
    def __init__(self):
        self.last = None
        self.first = None
        self.anchor = None

    def read(self, milliseconds, received_at):
        if milliseconds is None or not math.isfinite(milliseconds) or milliseconds < 0:
            return None
        seconds = milliseconds / 1000
        reset = self.last is not None and seconds < self.last
        if self.last is not None and seconds == self.last:
            return None
        previous = self.last
        if self.first is None or reset:
            self.first, self.anchor = seconds, received_at
        self.last = seconds
        delta = seconds - previous if previous is not None and not reset else 0
        return {'seconds':seconds,'utc':self.anchor + timedelta(seconds=seconds-self.first),
                'delta':delta,'reset':reset,'basis':'SOURCE_PTS_SESSION_ANCHORED_UTC'}
