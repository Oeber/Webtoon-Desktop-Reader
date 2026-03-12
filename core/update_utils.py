import time


UPDATE_COOLDOWN_SECONDS = 30


def cooldown_remaining(last_update_at: int | None, now: int | None = None) -> int:
    if last_update_at is None:
        return 0
    current_time = int(time.time()) if now is None else int(now)
    elapsed = current_time - int(last_update_at)
    return max(0, UPDATE_COOLDOWN_SECONDS - elapsed)
