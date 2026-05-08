"""One-time on-chain approvals for Polymarket neg-risk markets.

BTC 15-minute UP/DOWN markets settle through the Neg-Risk Adapter, which
reads from the Neg-Risk Exchange in addition to the standard CTF Exchange.
For an order to FILL (not just post) we need:

  ERC-20 USDC.native allowance(maxUint256) for:
    - CTF Exchange
    - Neg-Risk Exchange
    - Neg-Risk Adapter

  ERC-1155 setApprovalForAll(true) on the CTF token for:
    - CTF Exchange
    - Neg-Risk Exchange
    - Neg-Risk Adapter

Idempotent: skips entries already approved (allowance >= 2**255).

References (Polymarket developer docs, Polygon mainnet):
  USDC native:        0x3c499c542cef5e3811e1192ce70d8cc03d5c3359
  CTF Exchange:       0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
  Neg-Risk Exchange:  0xC5d563A36AE78145C45a50134d48A1215220f80a
  Neg-Risk Adapter:   0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
  CTF (ERC-1155):     0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
"""

from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)

USDC_ADDRESS = Web3.to_checksum_address("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359")
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")
NEG_RISK_ADAPTER = Web3.to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")
CTF_TOKEN = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

SPENDERS = (CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER)
MAX_UINT = (1 << 256) - 1
LARGE_ALLOWANCE_THRESHOLD = 1 << 255

_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

_ERC1155_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
]


@dataclass
class ApprovalCheck:
    contract: str
    spender: str
    needed: bool
    note: str


def _w3(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def check_all(rpc_url: str, address: str) -> list[ApprovalCheck]:
    """Return current approval state without sending any transactions."""
    w3 = _w3(rpc_url)
    owner = Web3.to_checksum_address(address)
    usdc = w3.eth.contract(address=USDC_ADDRESS, abi=_ERC20_ABI)
    ctf = w3.eth.contract(address=CTF_TOKEN, abi=_ERC1155_ABI)

    out: list[ApprovalCheck] = []
    for spender in SPENDERS:
        allowance = usdc.functions.allowance(owner, spender).call()
        out.append(
            ApprovalCheck(
                contract="USDC",
                spender=spender,
                needed=allowance < LARGE_ALLOWANCE_THRESHOLD,
                note=f"allowance={allowance}",
            )
        )
    for spender in SPENDERS:
        approved = ctf.functions.isApprovedForAll(owner, spender).call()
        out.append(
            ApprovalCheck(
                contract="CTF",
                spender=spender,
                needed=not approved,
                note=f"isApprovedForAll={approved}",
            )
        )
    return out


def ensure_all(rpc_url: str, private_key: str, dry_run: bool = False) -> list[ApprovalCheck]:
    """Send the missing approvals. Returns the pre-update check list."""
    w3 = _w3(rpc_url)
    account = w3.eth.account.from_key(private_key)
    owner = account.address
    checks = check_all(rpc_url, owner)

    pending = [c for c in checks if c.needed]
    log.info("approvals.check", total=len(checks), pending=len(pending))

    if dry_run or not pending:
        return checks

    usdc = w3.eth.contract(address=USDC_ADDRESS, abi=_ERC20_ABI)
    ctf = w3.eth.contract(address=CTF_TOKEN, abi=_ERC1155_ABI)
    nonce = w3.eth.get_transaction_count(owner)

    for chk in pending:
        if chk.contract == "USDC":
            tx = usdc.functions.approve(chk.spender, MAX_UINT).build_transaction(
                {"from": owner, "nonce": nonce, "chainId": w3.eth.chain_id}
            )
        else:  # CTF
            tx = ctf.functions.setApprovalForAll(chk.spender, True).build_transaction(
                {"from": owner, "nonce": nonce, "chainId": w3.eth.chain_id}
            )
        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info("approvals.sent", contract=chk.contract, spender=chk.spender, tx=h.hex())
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        if receipt.status != 1:
            raise RuntimeError(f"Approval tx {h.hex()} reverted")
        nonce += 1

    return checks
