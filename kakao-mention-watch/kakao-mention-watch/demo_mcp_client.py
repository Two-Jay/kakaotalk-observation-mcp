"""Headless MCP client: intentionally has no screenshot/mouse/keyboard code."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(args):
    server = StdioServerParameters(command=sys.executable, args=[
        str(Path(__file__).with_name("collector_mcp.py")),
        "--room", args.room, "--output", str(args.output.resolve())])
    report = {"client_has_computer_use": False, "transport": "stdio", "calls": []}
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            report["server"] = initialized.serverInfo.model_dump()
            report["tools"] = [t.name for t in (await session.list_tools()).tools]
            names = (["inspect_kakao", "open_configured_room", "collect_messages", "collect_messages"]
                     if args.live else []) + ["get_collector_status"]
            for name in names:
                result = await session.call_tool(name, {})
                entry = {"tool": name, "is_error": bool(result.isError),
                         "result": result.structuredContent,
                         "content": [c.text for c in result.content if c.type == "text"]}
                report["calls"].append(entry)
                print(json.dumps(entry, ensure_ascii=False), flush=True)
                if result.isError:
                    break
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "mcp-verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(c["is_error"] for c in report["calls"]):
        raise RuntimeError(f"MCP 검증 실패: {path}")
    print(json.dumps({"verification_file": str(path.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--room", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--live", action="store_true", help="Opt in to live search/open and two captures")
    asyncio.run(run(p.parse_args()))
