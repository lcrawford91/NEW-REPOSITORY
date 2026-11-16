"""Read-only historical Ethereum V2 reserve scanner. Never signs or broadcasts transactions."""
import json
import os
import re
from pathlib import Path
import urllib.request
import urllib.error
import socket
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
FACTORIES = {
    "UniswapV2": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    "SushiSwapV2": "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
}
BPS = 10000
U6 = 10**6

def word(addr):
    if len(addr) != 42 or not addr.startswith("0x"):
        raise ValueError("Invalid address")
    int(addr[2:], 16)
    return addr[2:].lower().zfill(64)

class RpcFailure(Exception):
    def __init__(self, category, message, http_status=502):
        super().__init__(message)
        self.category = category
        self.http_status = http_status

def rpc_error_category(status=None, code=None, message=""):
    # Never return provider messages verbatim: they may contain API credentials.
    m = str(message).lower()
    if status in (404, 405, 501):
        return "invalid_endpoint", "ETH_RPC points to an address that does not support Ethereum JSON-RPC POST. Use the full Alchemy Ethereum Mainnet HTTPS endpoint including /v2/<API_KEY>.", 502
    if status in (401, 403) or "invalid api key" in m or "invalid app key" in m or "unauthorized" in m or "invalid token" in m or "must be authenticated" in m:
        return "authentication", "Alchemy rejected the RPC credentials. Rotate the API key and update ETH_RPC.", 502
    if status == 429 or code == 429 or "rate limit" in m or "too many requests" in m or "capacity" in m or "compute units" in m:
        return "rate_limit", "RPC rate or capacity limit reached; check provider quota.", 503
    if "archive" in m or "historical state" in m or "missing trie node" in m or "state unavailable" in m or "state is not available" in m or "pruned" in m or "not available at block" in m:
        return "archive_unavailable", "Provider cannot serve historical state for that block; check archive access.", 502
    if status and status >= 500:
        return "provider_unavailable", "Ethereum RPC provider returned a server error.", 502
    return "rpc_rejected", "Ethereum RPC rejected the request; verify endpoint, network and provider access.", 502

def rpc(url, method, params):
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(2048)
            data = json.loads(raw)
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("message", "") if isinstance(err, dict) else ""
        except Exception:
            msg = ""
        category, explanation, http_status = rpc_error_category(status=status, message=msg)
        raise RpcFailure(category, explanation + " (upstream HTTP " + str(status) + " during " + method + ")", http_status) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror) or "name resolution" in str(reason).lower():
            raise RpcFailure("dns", "Cannot resolve the RPC provider hostname.") from None
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            raise RpcFailure("timeout", "RPC provider did not respond before timeout.", 504) from None
        raise RpcFailure("connection", "Cannot connect to the RPC provider.") from None
    except (ValueError, json.JSONDecodeError):
        raise RpcFailure("invalid_json", "RPC provider returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise RpcFailure("invalid_response", "RPC returned an unexpected response.")
    if "error" in result:
        err = result.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message", "") if isinstance(err, dict) else ""
        category, explanation, http_status = rpc_error_category(code=code, message=msg)
        raise RpcFailure(category, explanation, http_status)
    if "result" not in result:
        raise RpcFailure("missing_result", "RPC response has no result field.")
    return result["result"]

def call(url, addr, data, block):
    return rpc(url, "eth_call", [{"to":addr,"data":data},block])

def address_from(raw):
    if not isinstance(raw, str) or not raw.startswith("0x") or len(raw) < 66:
        raise ValueError("Invalid address response")
    return "0x" + raw[-40:].lower()

def get_snapshot(url, number):
    if int(rpc(url, "eth_chainId", []), 16) != 1:
        raise ValueError("RPC is not Ethereum Mainnet")
    block = hex(number)
    meta = rpc(url, "eth_getBlockByNumber", [block, False])
    if not meta or int(meta["number"], 16) != number:
        raise ValueError("Historical block unavailable")
    pools = {}
    for name, factory in FACTORIES.items():
        pair = address_from(call(url, factory, "0xe6a43905"+word(WETH)+word(USDC), block))
        if int(pair[2:], 16) == 0:
            raise ValueError("Pool was not deployed at that block")
        t0 = address_from(call(url, pair, "0x0dfe1681", block))
        t1 = address_from(call(url, pair, "0xd21220a7", block))
        if {t0.lower(),t1.lower()} != {WETH.lower(),USDC.lower()}:
            raise ValueError("Pool token mismatch")
        raw = call(url, pair, "0x0902f1ac", block)
        if not isinstance(raw, str) or not raw.startswith("0x") or len(raw) < 194:
            raise ValueError("Malformed reserve response")
        r0, r1 = int(raw[2:66],16), int(raw[66:130],16)
        if not r0 or not r1:
            raise ValueError("Zero pool reserves")
        pools[name] = {"weth":r0 if t0 == WETH.lower() else r1,
                       "usdc":r0 if t0 == USDC.lower() else r1}
    return {"chain_id":1,"block":number,"timestamp":int(meta["timestamp"],16),"block_hash":meta["hash"],"pools":pools}

def quote(amount, reserve_in, reserve_out):
    if amount <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amt = amount * 997
    return amt * reserve_out // (reserve_in * 1000 + amt)

def analyse(snapshot, gas_gwei):
    output = []
    names = list(FACTORIES)
    for buy,sell in ((names[0],names[1]),(names[1],names[0])):
        b,s = snapshot["pools"][buy], snapshot["pools"][sell]
        for size in (1000,5000,10000,25000):
            initial = size * U6
            weth = quote(initial,b["usdc"],b["weth"])
            final = quote(weth,s["weth"],s["usdc"])
            premium = (initial*5+BPS-1)//BPS
            after_slippage = final*(BPS-30)//BPS
            gas_wei = 350000*gas_gwei*10**9
            gas_usdc = gas_wei*s["usdc"]//s["weth"]
            net = after_slippage-initial-premium-gas_usdc
            spot_weth = initial*997*b["weth"]//(1000*b["usdc"])
            spot_usdc = weth*997*s["usdc"]//(1000*s["weth"])
            impact1 = max(0,(spot_weth-weth)*BPS//spot_weth) if spot_weth else BPS
            impact2 = max(0,(spot_usdc-final)*BPS//spot_usdc) if spot_usdc else BPS
            impact = max(impact1,impact2)
            output.append({"buy_on":buy,"sell_on":sell,"loan_usdc":size,
                           "net_estimate_usdc":str(net / U6),
                           "max_price_impact_bps":impact,
                           "screen_pass":net > 0 and impact <= 100})
    return output

def scan(params):
    # Prefer the dedicated Alchemy secret; keep legacy ETH_RPC compatibility.
    url = (os.environ.get("ALCHEMY_API_KEY") or os.environ.get("ETH_RPC") or "").strip()
    if not url:
        return 503, {"error":"Alchemy not configured. Add ALCHEMY_API_KEY (or ETH_RPC) as a private Vercel environment variable.", "category":"configuration"}
    if not url.startswith("https://"):
        # Accept a raw Alchemy app key configured as a sensitive server-side variable.
        # Never expose the constructed URL or secret in responses or logs.
        if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", url):
            url = "https://eth-mainnet.g.alchemy.com/v2/" + url
        else:
            return 503, {"error":"ETH_RPC must be an Alchemy API key or a full HTTPS Ethereum RPC endpoint."}
    try:
        block = int(params.get("block",["25733035"])[0])
        gas = int(params.get("gas_gwei",["10"])[0])
        if not 10000000 <= block <= 100000000 or not 0 <= gas <= 1000:
            return 400, {"error":"Invalid block number or assumed gas price"}
        snap = get_snapshot(url,block)
        return 200, {"mode":"READ_ONLY_HISTORICAL_RESERVE_SCREEN",
                      "warning":"Indicative historical reserve estimates only, not executable or realised profits.",
                      "snapshot":snap,"results":analyse(snap,gas)}
    except RpcFailure as exc:
        return exc.http_status, {"error":str(exc), "category":exc.category}
    except (ValueError,TypeError):
        return 400, {"error":"Invalid block, RPC chain, or historical pool data", "category":"validation"}
    except Exception:
        return 502, {"error":"Historical scanner encountered an internal error.", "category":"internal"}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = Path(__file__).resolve().parent.parent / "index.html"
            try:
                body = page.read_bytes()
                status = 200
            except OSError:
                body = b"Website assets unavailable"
                status = 503
            content_type = "text/html; charset=utf-8"
        else:
            if parsed.path != "/api/scan":
                status,data = 404,{"error":"Not found"}
            else:
                status,data = scan(parse_qs(parsed.query))
            body = json.dumps(data).encode()
            content_type = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type",content_type)
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
