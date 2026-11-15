"""Clock-radio things: the alarms and the sleep timer.

    alarm = Alarm.from_dict(settings["alarms"][0])
    alarm.due(time.localtime())     -> True once, at the set minute, on its days
    next_alarm(alarms, time.localtime())   -> the one that rings next, or None
    timer = SleepTimer()
    timer.set(30, now)              # minutes; 0 turns it off
    timer.phase(now)                -> None | "running" | "fading" (its last FADE_S) | "done"

The alarm wakes the radio to the station that was playing when it was set, fading in over
ALARM_FADE_S, and shows a card: the Volume button snoozes it for SNOOZE_MIN (the radio goes
quiet and dark, then the alarm comes back, fading in over SNOOZE_FADE_S), the Tune button turns
it off and leaves the station playing. The sleep timer fades the station out over its last
FADE_S and puts the radio in standby.
"""

ALARM_FADE_S = 60
SNOOZE_MIN = 9
SNOOZE_FADE_S = 10            # a snoozed alarm comes back sooner than it first faded in
EVERY_DAY = tuple(range(7))   # days are time.localtime()'s tm_wday: Monday is 0, Sunday 6
FADE_S = 60
STANDBY_FADE_S = 1.5          # standby fades the screen and the sound out, and waking fades them in


class Alarm:
    def __init__(self, on=False, hour=7, minute=0, station=None, days=EVERY_DAY):
        self.on, self.hour, self.minute = on, hour, minute
        self.station = station        # {"name", "place", "stationuuid", "geo_lat", "geo_long"}
        self.days = sorted({int(d) % 7 for d in days})
        self._rang = None             # the day it last rang

    @classmethod
    def from_dict(cls, d, default=None):
        """From settings.json; `default` (an Alarm) when there's nothing usable there."""
        if not d:
            return default or cls()
        try:
            return cls(bool(d.get("on")), int(d.get("hour", 7)) % 24, int(d.get("minute", 0)) % 60,
                       d.get("station"), d.get("days", EVERY_DAY))
        except (TypeError, ValueError):
            return default or cls()

    def to_dict(self):
        return {"on": self.on, "hour": self.hour, "minute": self.minute, "days": self.days,
                "station": self.station}

    @property
    def minutes(self):
        """Minutes after midnight when it's on, else None."""
        return self.hour * 60 + self.minute if self.on else None

    def due(self, local):
        """local: time.localtime(). True once, in the alarm's minute, on one of its days."""
        today = (local.tm_year, local.tm_yday)
        if (self.on and local.tm_wday in self.days and (local.tm_hour, local.tm_min) == (self.hour, self.minute)
                and self._rang != today):
            self._rang = today
            return True
        return False

    def minutes_until(self, local):
        """Minutes from `local` until it next rings (within a week), or None if it won't."""
        if not self.on or not self.days:
            return None
        now = local.tm_hour * 60 + local.tm_min
        for ahead in range(8):
            if (local.tm_wday + ahead) % 7 in self.days and (ahead or self.minutes > now):
                return ahead * 1440 + self.minutes - now
        return None


def next_alarm(alarms, local):
    """The alarm that rings next, or None."""
    waits = [(a.minutes_until(local), i) for i, a in enumerate(alarms)]
    waits = [w for w in waits if w[0] is not None]
    return alarms[min(waits)[1]] if waits else None


class SleepTimer:
    def __init__(self):
        self.ends = None

    def set(self, minutes, now):
        self.ends = now + minutes * 60 if minutes else None

    def left(self, now):
        """Seconds left, or None when it's off."""
        return None if self.ends is None else max(0.0, self.ends - now)

    def minutes_left(self, now):
        left = self.left(now)
        return None if left is None else int(-(-left // 60))      # rounded up: "1 min" until the end

    def phase(self, now):
        left = self.left(now)
        if left is None:
            return None
        return "done" if left <= 0 else "fading" if left <= FADE_S else "running"
