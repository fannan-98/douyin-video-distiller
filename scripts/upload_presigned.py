#!/usr/bin/env python3
"""PUT a local file to an ima knowledge-base presigned URL.

Uses http.client for a direct connection: urllib reads the Windows registry
proxy on this machine, which can break the TLS handshake.
"""

import argparse
import http.client
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description="Upload bytes to a presigned URL")
    parser.add_argument("--file", required=True, help="local file to upload")
    parser.add_argument("--url", required=True, help="presigned_url from create_media")
    parser.add_argument("--headers-json", required=True,
                        help="upload_url.auth_headers serialized as a JSON object")
    parser.add_argument("--content-type", default="text/markdown",
                        help="MIME type, must match create_media content_type")
    parser.add_argument("--expected-size", type=int, default=0,
                        help="file_size passed to create_media; 0 means auto-check skipped")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(json.dumps({"ok": False, "error": "file_not_found", "path": str(path)},
                         ensure_ascii=False, indent=2))
        return 2

    data = path.read_bytes()
    size = len(data)

    try:
        headers = json.loads(args.headers_json)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": "invalid_headers_json",
                          "detail": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    if not isinstance(headers, dict):
        print(json.dumps({"ok": False, "error": "headers_must_be_object"},
                         ensure_ascii=False, indent=2))
        return 2

    # Preserve every auth header verbatim. Only Content-Type / Content-Length
    # are reconciled, because the signature covers them.
    declared = headers.get("Content-Length") or headers.get("content-length")
    if declared is not None and str(declared) != str(size):
        print(json.dumps({"ok": False, "error": "content_length_mismatch",
                          "declared": str(declared), "actual": size,
                          "hint": "Re-run create_media with file_size=" + str(size)},
                         ensure_ascii=False, indent=2))
        return 2
    if args.expected_size and args.expected_size != size:
        print(json.dumps({"ok": False, "error": "expected_size_mismatch",
                          "expected": args.expected_size, "actual": size},
                         ensure_ascii=False, indent=2))
        return 2

    headers = dict(headers)
    headers["Content-Length"] = str(size)
    if not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = args.content_type

    parts = urlsplit(args.url)
    if not parts.hostname:
        print(json.dumps({"ok": False, "error": "invalid_url"}, ensure_ascii=False, indent=2))
        return 2
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection

    result = {"file": str(path), "size": size, "content_type": args.content_type,
              "host": parts.hostname, "url_scheme": parts.scheme}
    try:
        conn = conn_cls(parts.hostname, port, timeout=args.timeout)
        try:
            conn.request("PUT", target, body=data, headers=headers)
            resp = conn.getresponse()
            body = resp.read(2048).decode("utf-8", "replace")
            result.update({"status": resp.status, "reason": resp.reason,
                           "response_excerpt": body})
            result["ok"] = 200 <= resp.status < 300
            if not result["ok"]:
                result["hint"] = ("Presigned PUT failed. Check that auth_headers were "
                                  "passed verbatim and that the link has not expired; "
                                  "re-run create_media if needed.")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - surface any transport failure as JSON
        result.update({"ok": False, "error": "transport_error", "detail": str(exc)})

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
