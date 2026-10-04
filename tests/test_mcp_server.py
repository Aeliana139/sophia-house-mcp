#!/usr/bin/env python3
"""
Tests for the sophia-house-mcp stdio server (mcp_server.py).

Every declared tool is exercised through the real JSON-RPC dispatch path
(_handle -> TOOL_FN -> tool function). Upstream HTTP is faked by patching
mcp_server._http_json so tests are hermetic (no network, no live doors).

Run:
    python3 -m unittest discover -s tests -v
    # or
    python3 -m unittest tests.test_mcp_server -v
"""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_server  # noqa: E402

ALL_TOOLS = ("verify_settlement", "anchor_receipt", "book_status")
ANNOTATIONS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

EX_TXID = "2c97f194f31a19bef68d85e63c92874b14551a15bd3834340f67a423528dd9ae"


def rpc(method, params=None, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def call_tool(name, args, msg_id=1):
    return _handle_wrap(rpc("tools/call", {"name": name, "arguments": args}, msg_id))


def _handle_wrap(msg):
    resp = mcp_server._handle(msg)
    return json.loads(json.dumps(resp))  # round-trip like the stdio line


class ServerProtocolTest(unittest.TestCase):
    def test_initialize(self):
        resp = _handle_wrap(rpc("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(resp["result"]["serverInfo"]["name"], mcp_server.SERVER_NAME)
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_ping_roundtrip(self):
        resp = _handle_wrap(rpc("ping"))
        self.assertEqual(resp["result"], {})

    def test_unknown_tool_is_error(self):
        resp = _handle_wrap(rpc("tools/call", {"name": "nope", "arguments": {}}))
        self.assertIsNotNone(resp.get("error"))
        self.assertEqual(resp["error"]["code"], -32602)

    def test_unknown_method_is_error(self):
        resp = _handle_wrap(rpc("bogus/method"))
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_gets_no_response(self):
        self.assertIsNone(mcp_server._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class ToolDeclarationTest(unittest.TestCase):
    def test_all_tools_declared_with_all_four_annotations(self):
        resp = _handle_wrap(rpc("tools/list"))
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        self.assertEqual(set(tools), set(ALL_TOOLS))
        for name in ALL_TOOLS:
            ann = tools[name].get("annotations", {})
            for hint in ANNOTATIONS:
                self.assertIn(hint, ann, f"{name} missing annotation {hint}")
                self.assertIs(type(ann[hint]), bool, f"{name} annotation {hint} must be a bool")
        # read-only server: all three tools are pure reads
        for name in ALL_TOOLS:
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[name]["annotations"]["destructiveHint"])
            self.assertFalse(tools[name]["annotations"]["openWorldHint"])


class VerifySettlementTest(unittest.TestCase):
    def test_mined_tx_returns_verdict(self):
        door = {"status": 200, "body": {"verdict": "MINED", "height": 492692}}
        with mock.patch.object(mcp_server, "_http_json", return_value=(200, door["body"])) as http:
            resp = call_tool("verify_settlement", {"txid": EX_TXID})
            self.assertFalse(resp["result"]["isError"])
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["http_status"], 200)
            self.assertEqual(text["result"]["verdict"], "MINED")
            # correct door route + payload
            method, url = http.call_args[0][0], http.call_args[0][1]
            self.assertEqual(method, "POST")
            self.assertEqual(url, f"{mcp_server.SOCSEAL_BASE}/verify/settlement")
            self.assertEqual(http.call_args[0][2], {"txid": EX_TXID})

    def test_unknown_tx_returns_honest_failure(self):
        with mock.patch.object(mcp_server, "_http_json", return_value=(404, {"error": "not found"})):
            resp = call_tool("verify_settlement", {"txid": EX_TXID})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["http_status"], 404)

    def test_bad_txid_returns_clean_error(self):
        with mock.patch.object(mcp_server, "_http_json", return_value=(200, {})) as http:
            resp = call_tool("verify_settlement", {"txid": "nothex"})
            self.assertTrue(resp["result"]["isError"])
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertIn("error", text)
            http.assert_not_called()  # never hit the door with garbage


class AnchorReceiptTest(unittest.TestCase):
    def test_keeper_status_and_book_anchor_lookup(self):
        keeper = (200, {"txid": EX_TXID, "height": 492692, "status": "MINED"})
        book = (200, {"day": {"day": 1, "anchor": {"txid": EX_TXID, "block": 492692,
                                                   "atoms": 9, "text": "sealed", "verify_status": "verified"}}})
        with mock.patch.object(mcp_server, "_http_json", side_effect=[keeper, book]) as http:
            resp = call_tool("anchor_receipt", {"txid": EX_TXID})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["keeper_status"], 200)
            self.assertEqual(text["mined_status"]["height"], 492692)
            self.assertEqual(text["book_anchor"]["day"], 1)
            self.assertEqual(text["book_anchor"]["block"], 492692)
            # keeper probe first, then book day probe
            self.assertEqual(http.call_args_list[0][0][1], f"{mcp_server.KEEPER_BASE}/tx/{EX_TXID}")
            self.assertEqual(http.call_args_list[1][0][1], f"{mcp_server.BOOK_BASE}/book/day/1")

    def test_unknown_tx_no_book_probe(self):
        keeper = (200, {"txid": EX_TXID, "height": 0})  # not mined -> skip anchor probe
        with mock.patch.object(mcp_server, "_http_json", side_effect=[keeper]) as http:
            resp = call_tool("anchor_receipt", {"txid": EX_TXID})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(http.call_count, 1)
            self.assertNotIn("book_anchor", text)

    def test_bad_txid_never_reaches_keeper(self):
        with mock.patch.object(mcp_server, "_http_json", return_value=(200, {})) as http:
            resp = call_tool("anchor_receipt", {"txid": "z"})
            self.assertTrue(resp["result"]["isError"])
            http.assert_not_called()


class BookStatusTest(unittest.TestCase):
    def test_returns_clearing_and_anchor(self):
        book = (200, {"day": {"day": 7, "status": "closed",
                              "roots": {"open": "a", "close": "b"},
                              "clearing": {"D": 1, "S": 2, "V": 3, "p_star": 4, "cap": 5, "depth": 6, "wash_penalty": 7},
                              "anchor": {"block": 492692, "txid": EX_TXID}}})
        with mock.patch.object(mcp_server, "_http_json", return_value=book) as http:
            resp = call_tool("book_status", {"day": 7})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["day"], 7)
            self.assertEqual(text["status"], "closed")
            self.assertEqual(text["clearing"]["D"], 1)
            self.assertEqual(text["anchor"]["block"], 492692)
            self.assertEqual(http.call_args[0][1], f"{mcp_server.BOOK_BASE}/book/day/7")

    def test_default_day_is_one(self):
        with mock.patch.object(mcp_server, "_http_json", return_value=(200, {"day": {"day": 1}})) as http:
            resp = call_tool("book_status", {})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["day"], 1)
            self.assertIn("/book/day/1", http.call_args[0][1])

    def test_upstream_down_is_honest_error_not_fake(self):
        with mock.patch.object(mcp_server, "_http_json",
                               return_value=(0, {"error": "unreachable: refused"})):
            resp = call_tool("book_status", {"day": 1})
            text = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(text["http_status"], 0)
            self.assertIn("error", text["result"])


class StdioSubprocessTest(unittest.TestCase):
    """Full wire test: spawn the real server over stdin/stdout pipes."""

    def test_initialize_list_call_roundtrip(self):
        env = dict(os.environ, SOCSEAL_BASE="http://127.0.0.1:9",  # unreachable port:
                   BOOK_BASE="http://127.0.0.1:9", KEEPER_BASE="http://127.0.0.1:9")  # must stay honest
        proc = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            msgs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "book_status", "arguments": {"day": 1}}},
            ]
            payload = "".join(json.dumps(m) + "\n" for m in msgs).encode()
            out, err = proc.communicate(payload, timeout=30)
            lines = [json.loads(l) for l in out.decode().splitlines() if l.strip()]
        finally:
            proc.kill()
        by_id = {m["id"]: m for m in lines}
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "socseal-storefront")
        tools = {t["name"]: t for t in by_id[2]["result"]["tools"]}
        self.assertEqual(set(tools), set(ALL_TOOLS))
        for name in ALL_TOOLS:
            self.assertEqual(set(tools[name]["annotations"]), set(ANNOTATIONS))
        self.assertEqual(by_id[3]["result"], {})
        # book_status against an unreachable door must be an honest error, never fake data
        text = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertEqual(text["http_status"], 0)
        self.assertIn("error", text["result"])


if __name__ == "__main__":
    unittest.main()