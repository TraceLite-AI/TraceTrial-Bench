"""重试。max_retries=N 表示首次之外再试 N 次，共 N+1 次调用。退避有上限。"""


def call_with_retry(fn, max_retries=3, base_delay=1.0, max_delay=8.0):
    attempts = 0
    delays = []
    last = None
    while attempts < max_retries:
        try:
            return fn(), attempts + 1, delays
        except Exception as e:
            last = e
            if attempts == max_retries:
                break
            delays.append(min(base_delay * (2 ** attempts), max_delay))
            attempts += 1
    raise last
