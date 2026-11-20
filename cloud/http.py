import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CloudRequestError(RuntimeError):
    def __init__(self, message, status=None, details=None):
        super().__init__(message)
        self.status = status
        self.details = details


def request_json(method, url, headers=None, body=None, timeout=30):
    request_headers = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=payload, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        try:
            details = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            details = {}
        message = (
            details.get("msg")
            or details.get("message")
            or details.get("error_description")
            or details.get("error")
            or f"Supabase request failed with HTTP {exc.code}."
        )
        raise CloudRequestError(str(message), status=exc.code, details=details) from exc
    except (URLError, TimeoutError) as exc:
        raise CloudRequestError("Could not reach Supabase. Check the network connection.") from exc
