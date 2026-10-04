#!/usr/bin/env python3
"""
socseal MCP wrapper — read-only Model Context Protocol (stdio) server.

Lets MCP-native agents "shop" the socseal settlement doors: verify a txid mined,
look up an anchor receipt, and read the public settlement book — all read-only,
never spends, never writes.

Transport: MCP stdio (JSON-RPC 2.0, newline-delimited on stdin/stdout).
Protocol: initialize -> notifications/initialized -> tools/list -> tools/call.

Run:
    python3 mcp_server.py                     # loopback defaults (this box)
    SOCSEAL_BASE=https://socseal.xyz \
    BOOK_BASE=https://socseal.xyz \
    KEEPER_BASE=... \
        python3 mcp_server.py                 # point at the public front

No third-party dependencies (stdlib only: json, sys, urllib).

HONESTY NOTES (kept deliberately read-only):
- verify_settlement reports the verdict + evidence returned by the door. On the
  public front the paid flavor needs the x402 payment gate; this wrapper never
  pays — it surfaces the verdict the door returns (loopback returns the signed
  proof directly; public returns the payment challenge once free trials are used).
- anchor_receipt reports SEPTA mined-status from the keeper; it does not create
  anchors and does not spend the 9-atom stamp.
- book_status reads the public mirror only.
"""

import json
import os
import sys
import urllib.request
import urllib.error

# --------------------------------------------------------------------------
# configuration (loopback by default = the live services on this box)
# --------------------------------------------------------------------------
SOCSEAL_BASE = os.environ.get("SOCSEAL_BASE", "http://127.0.0.1:18092").rstrip("/")
BOOK_BASE    = os.environ.get("BOOK_BASE",    "http://127.0.0.1:8382").rstrip("/")
KEEPER_BASE  = os.environ.get("KEEPER_BASE",  "http://127.0.0.1:18181").rstrip("/")

SERVER_NAME = "socseal-storefront"
SERVER_VERSION = "0.1.0"

TOOLS = [
    {
        "name": "verify_settlement",
        "description": (
            "Prove a transaction id actually MINED on-chain (SEPTA: height>0 and out of "
            "mempool). Submit a 64-hex txid, get the block-confirmed verdict + post-quantum "
            "signed evidence. Fail-closed: unknown/never-mined ids return a clean negative, "
            "never a fake success. Read-only; does not pay."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "txid": {"type": "string", "description": "64-hex transaction id"},
            },
            "required": ["txid"],
        },
    },
    {
        "name": "anchor_receipt",
        "description": (
            "Confirm an anchor stamp is on-chain: given a txid, report SEPTA mined status "
            "(height>0, out of mempool) from the keeper, and if that txid is a known settlement-book "
            "anchor, return its full record (block, atoms, sealed text, verify status). Read-only."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "txid": {"type": "string", "description": "64-hex transaction id (e.g. a book anchor txid)"},
            },
            "required": ["txid"],
        },
    },
    {
        "name": "book_status",
        "description": (
            "Read the public settlement book for a day: day number, status, merkle roots "
            "(root_open/root_close), clearing summary and the on-chain anchor (block, txid, "
            "verify status). Read-only public mirror. Use this to audit the venue's market history."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "day": {"type": "integer", "description": "book day number (1 = first day)", "default": 1},
            },
        },
    },
]

# --------------------------------------------------------------------------
# small HTTP helpers
# --------------------------------------------------------------------------
def _http_json(method, url, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null")
        except Exception:
            return e.code, {"http_error": e.code}
    except Exception as e:  # connection refused etc.
        return 0, {"error": f"unreachable: {e}"}

# --------------------------------------------------------------------------
# tool implementations (READ-ONLY)
# --------------------------------------------------------------------------
def tool_verify_settlement(args):
    txid = (args or {}).get("txid", "").strip()
    if not txid or len(txid) != 64 or any(c not in "0123456789abcdefABCDEF" for c in txid):
        return {"error": "txid must be 64-hex"}
    status, body = _http_json("POST", f"{SOCSEAL_BASE}/verify/settlement",
                              {"txid": txid})
    return {"http_status": status, "result": body}

def tool_anchor_receipt(args):
    txid = (args or {}).get("txid", "").strip()
    if not txid or len(txid) != 64 or any(c not in "0123456789abcdefABCDEF" for c in txid):
        return {"error": "txid must be 64-hex"}
    status, body = _http_json("GET", f"{KEEPER_BASE}/tx/{txid}")
    out = {"keeper_status": status, "mined_status": body}
    # is it a known settlement-book anchor? (day 1..9, cheap read-only probe)
    if status == 200 and body.get("height"):
        for day in range(1, 10):
            bs, rec = _http_json("GET", f"{BOOK_BASE}/book/day/{day}")
            if bs != 200:
                continue
            anchor = (rec or {}).get("day", {}).get("anchor") or {}
            if anchor.get("txid") == txid:
                out["book_anchor"] = {
                    "day": day,
                    "block": anchor.get("block"),
                    "atoms": anchor.get("atoms"),
                    "sealed_text": anchor.get("text"),
                    "verify_status": anchor.get("verify_status"),
                }
                break
    return out

def tool_book_status(args):
    day = int((args or {}).get("day", 1))
    status, rec = _http_json("GET", f"{BOOK_BASE}/book/day/{day}")
    if status != 200:
        return {"http_status": status, "result": rec}
    d = (rec or {}).get("day", {})
    return {
        "http_status": status,
        "day": d.get("day"),
        "status": d.get("status"),
        "roots": d.get("roots"),
        "clearing": {
            k: d.get("clearing", {}).get(k) for k in ("D", "S", "V", "p_star", "cap", "depth", "wash_penalty")
        },
        "anchor": d.get("anchor"),
    }

TOOL_FN = {
    "verify_settlement": tool_verify_settlement,
    "anchor_receipt": tool_anchor_receipt,
    "book_status": tool_book_status,
}

# --------------------------------------------------------------------------
# JSON-RPC / MCP plumbing
# --------------------------------------------------------------------------
def _jsonrpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}

def _jsonrpc_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

def _handle(msg):
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _jsonrpc_result(msg_id, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _jsonrpc_result(msg_id, {})
    if method == "tools/list":
        return _jsonrpc_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        fn = TOOL_FN.get(name)
        if fn is None:
            return _jsonrpc_error(msg_id, -32602, f"unknown tool: {name}")
        try:
            result = fn(params.get("arguments") or {})
            text = json.dumps(result, indent=2)
            return _jsonrpc_result(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": bool(result.get("error")),
            })
        except Exception as e:
            return _jsonrpc_error(msg_id, -32603, f"tool error: {e}")
    if method.startswith("notifications/"):
        return None  # notification -> no response
    return _jsonrpc_error(msg_id, -32601, f"method not found: {method}")

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
