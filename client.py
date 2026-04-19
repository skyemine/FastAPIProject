from __future__ import annotations

import argparse
import asyncio
import contextlib
import json

import httpx
import websockets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnostic CLI client for direct chats in PulseChat.")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="HTTP server URL.")
    parser.add_argument("--username", required=True, help="Account username.")
    parser.add_argument("--password", required=True, help="Account password.")
    parser.add_argument("--friend", required=True, help="Friend username for direct chat.")
    parser.add_argument("--display-name", default="", help="Display name for auto-registration.")
    parser.add_argument("--register", action="store_true", help="Create the account before connecting.")
    return parser


def cookie_header_from_client(client: httpx.Client) -> str:
    return "; ".join(f"{name}={value}" for name, value in client.cookies.items())


async def receive_messages(websocket: websockets.ClientConnection) -> None:
    async for raw_message in websocket:
        payload = json.loads(raw_message)
        if payload["type"] == "history":
            friend = payload["friend"]
            print(f"\n--- Direct chat with {friend['display_name']} (@{friend['username']}) ---")
            for message in payload["messages"]:
                print(f"[{message['sent_at']}] {message['sender_display_name']}: {message['content']}")
            print("--- End history ---\n")
            continue
        if payload["type"] == "message":
            message = payload["message"]
            print(f"[{message['sent_at']}] {message['sender_display_name']}: {message['content']}")
            continue
        print(payload)


async def send_messages(websocket: websockets.ClientConnection) -> None:
    loop = asyncio.get_running_loop()
    while True:
        content = await loop.run_in_executor(None, input, "")
        content = content.strip()
        if not content:
            continue
        await websocket.send(json.dumps({"content": content}))


async def main() -> None:
    args = build_parser().parse_args()
    auth_payload = {
        "username": args.username,
        "password": args.password,
        "display_name": args.display_name or args.username,
    }

    with httpx.Client(base_url=args.server, timeout=10.0) as client:
        if args.register:
            register_response = client.post("/api/auth/register", json=auth_payload)
            if register_response.status_code not in {200, 201, 409}:
                raise SystemExit(register_response.text)

        login_response = client.post("/api/auth/login", json=auth_payload)
        if login_response.status_code != 200:
            raise SystemExit(login_response.text)

        cookie_header = cookie_header_from_client(client)

    websocket_base = args.server.replace("http://", "ws://").replace("https://", "wss://")
    websocket_url = f"{websocket_base.rstrip('/')}/ws/direct/{args.friend}"

    async with websockets.connect(websocket_url, additional_headers={"Cookie": cookie_header}) as websocket:
        receive_task = asyncio.create_task(receive_messages(websocket))
        send_task = asyncio.create_task(send_messages(websocket))
        done, pending = await asyncio.wait({receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()


if __name__ == "__main__":
    asyncio.run(main())
