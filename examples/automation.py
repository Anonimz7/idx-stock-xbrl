from __future__ import annotations

import argparse
import json

from firefox_bridge.client import FirefoxBridgeClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Control Firefox through the local bridge")
    parser.add_argument("url", help="HTTP or HTTPS URL to open")
    parser.add_argument("--click-ref", help="Element reference from a previous snapshot")
    args = parser.parse_args()

    client = FirefoxBridgeClient()
    status = client.status()
    if not status.get("connected"):
        raise RuntimeError("Firefox extension is not connected")

    tab = client.open_tab(args.url)
    tab_id = tab["id"]
    snapshot = client.snapshot(tab_id)

    if args.click_ref:
        client.click(tab_id, args.click_ref)
        snapshot = client.snapshot(tab_id)

    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
