
"""Polite single-worker HTTP session: sequential, random delay, retries, audit log."""
import random
import time
import logging
import requests

API_BASE = "https://indiacode.gov.in/server/api"
SITE_BASE = "https://indiacode.gov.in"

DEFAULT_HEADERS = {
    "User-Agent": "TVCA-Research/0.1 (+time-versioned central acts methodology pilot; polite single-worker)",
    "Accept": "application/json",
}

class PoliteSession:
    def __init__(self, delay_min=0.5, delay_max=1.2, max_retries=4, timeout=45, log=None):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update(DEFAULT_HEADERS)
        self.log = log or logging.getLogger("tvca.polite")
        self.request_count = 0
        self.error_count = 0

    def _polite_wait(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def get_json(self, url, params=None):
        return self._request("GET", url, params=params, want="json")

    def get_bytes(self, url, params=None):
        return self._request("GET", url, params=params, want="bytes")

    def _request(self, method, url, params=None, want="json"):
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            self._polite_wait()
            try:
                r = self.s.request(method, url, params=params, timeout=self.timeout, verify=True)
                self.request_count += 1
                if r.status_code == 200:
                    if want == "json":
                        return r.json(), {"status": 200, "url": r.url, "bytes": len(r.content)}
                    return r.content, {"status": 200, "url": r.url, "bytes": len(r.content)}
                if r.status_code in (429, 500, 502, 503, 504):
                    self.error_count += 1
                    self.log.warning("retryable %s %s -> %s (attempt %d)", method, url, r.status_code, attempt)
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(min(30, 2 ** attempt + random.uniform(0, 1)))
                    continue
                # non-retryable: record and raise
                self.error_count += 1
                self.log.error("non-retryable %s %s -> %s", method, url, r.status_code)
                raise RuntimeError(f"HTTP {r.status_code} for {url}: {r.text[:500]}")
            except (requests.ConnectionError, requests.Timeout) as e:
                self.error_count += 1
                last_err = str(e)[:300]
                self.log.warning("connection issue %s (attempt %d): %s", url, attempt, last_err)
                time.sleep(min(30, 2 ** attempt + random.uniform(0, 1)))
                continue
        raise RuntimeError(f"failed after {self.max_retries} attempts: {url}: {last_err}")
