"""Read-only historical Ethereum V2 reserve scanner. Never signs or broadcasts transactions."""
import json
import os
from pathlib import Path
import urllib.request
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

def rpc(url, method, params):
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=12) as response:
        result = json.load(response)
    if "error" in result or "result" not in result:
        raise RuntimeError("Archive RPC returned an error")
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
    url = os.environ.get("ETH_RPC","").strip()
    if not url:
        return 503, {"error":"RPC not configured. Add ETH_RPC in Vercel environment settings."}
    if not url.startswith("https://"):
        return 503, {"error":"ETH_RPC must be an HTTPS URL."}
    try:
        block = int(params.get("block",["25733035"])[0])
        gas = int(params.get("gas_gwei",["10"])[0])
        if not 10000000 <= block <= 100000000 or not 0 <= gas <= 1000:
            return 400, {"error":"Invalid block number or assumed gas price"}
        snap = get_snapshot(url,block)
        return 200, {"mode":"READ_ONLY_HISTORICAL_RESERVE_SCREEN",
                      "warning":"Indicative historical reserve estimates only, not executable or realised profits.",
                      "snapshot":snap,"results":analyse(snap,gas)}
    except (ValueError,TypeError):
        return 400, {"error":"Invalid block, RPC chain, or historical pool data"}
    except Exception:
        return 502, {"error":"Ethereum archive RPC query failed. Check archive access and provider status."}

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
