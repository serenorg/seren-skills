#!/usr/bin/env python3
"""Generated SkillForge Ledger runtime for ledger-signing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = []

CHUNK_SIZE = 255
ETH_CLA = 0xE0
INS_SIGN_TX = 0x04
INS_SIGN_PERSONAL_MESSAGE = 0x08
INS_SIGN_EIP712_HASHED_MESSAGE = 0x0C


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generated SkillForge Ledger runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute a real USB/HID signing flow against a connected Ledger device.",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_hex(value: str) -> str:
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) % 2 != 0:
        raise ValueError("hex payload must have an even number of characters")
    return text


def _parse_hex(value: str, *, name: str) -> bytes:
    cleaned = _clean_hex(value)
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid {name} hex payload") from exc


def _parse_fixed_hex(value: str, *, name: str, size: int) -> bytes:
    payload = _parse_hex(value, name=name)
    if len(payload) != size:
        raise ValueError(f"{name} must be exactly {size} bytes ({size * 2} hex chars)")
    return payload


def _encode_bip32_path(path: str) -> bytes:
    parts = [part for part in path.split("/") if part and part != "m"]
    if not parts:
        raise ValueError("derivation path cannot be empty")
    if len(parts) > 10:
        raise ValueError("derivation path is too deep")

    encoded = bytearray([len(parts)])
    for part in parts:
        hardened = part.endswith("'")
        value_text = part[:-1] if hardened else part
        if not value_text.isdigit():
            raise ValueError(f"invalid derivation component: {part!r}")
        value = int(value_text)
        if value < 0 or value >= 0x80000000:
            raise ValueError(f"invalid derivation component: {part!r}")
        if hardened:
            value |= 0x80000000
        encoded.extend(value.to_bytes(4, byteorder="big"))
    return bytes(encoded)


def _apdu(cla: int, ins: int, p1: int, p2: int, payload: bytes) -> bytes:
    if len(payload) > 255:
        raise ValueError("APDU payload exceeds 255 bytes")
    return bytes([cla, ins, p1, p2, len(payload)]) + payload


def _chunked_sign(
    *,
    dongle: Any,
    ins: int,
    first_chunk: bytes,
    remainder: bytes,
) -> bytes:
    first_payload = first_chunk + remainder[: max(0, CHUNK_SIZE - len(first_chunk))]
    sent = len(first_payload) - len(first_chunk)
    response = dongle.exchange(_apdu(ETH_CLA, ins, 0x00, 0x00, first_payload))

    offset = sent
    while offset < len(remainder):
        chunk = remainder[offset : offset + CHUNK_SIZE]
        offset += len(chunk)
        p1 = 0x80 if offset >= len(remainder) else 0x00
        response = dongle.exchange(_apdu(ETH_CLA, ins, p1, 0x00, chunk))

    return response


def _decode_signature(response: bytes) -> dict[str, str | int]:
    if len(response) < 65:
        raise RuntimeError(
            f"unexpected Ledger response length for signature: {len(response)} bytes"
        )

    r = response[0:32]
    s = response[32:64]
    v = response[64]
    return {
        "r": "0x" + r.hex(),
        "s": "0x" + s.hex(),
        "v": int(v),
        "signature_hex": "0x" + response[:65].hex(),
    }


def _decode_signature_vrs(response: bytes) -> dict[str, str | int]:
    if len(response) < 65:
        raise RuntimeError(
            f"unexpected Ledger response length for signature: {len(response)} bytes"
        )

    v = response[0]
    r = response[1:33]
    s = response[33:65]
    return {
        "r": "0x" + r.hex(),
        "s": "0x" + s.hex(),
        "v": int(v),
        "signature_hex": "0x" + (r + s + bytes([v])).hex(),
    }


def _sign_transaction(*, dongle: Any, derivation_path: str, payload_hex: str) -> dict[str, Any]:
    path_bytes = _encode_bip32_path(derivation_path)
    payload = _parse_hex(payload_hex, name="transaction")
    response = _chunked_sign(
        dongle=dongle,
        ins=INS_SIGN_TX,
        first_chunk=path_bytes,
        remainder=payload,
    )
    return _decode_signature(response)


def _sign_message(*, dongle: Any, derivation_path: str, payload_hex: str) -> dict[str, Any]:
    path_bytes = _encode_bip32_path(derivation_path)
    message = _parse_hex(payload_hex, name="message")
    header = path_bytes + len(message).to_bytes(4, byteorder="big")
    response = _chunked_sign(
        dongle=dongle,
        ins=INS_SIGN_PERSONAL_MESSAGE,
        first_chunk=header,
        remainder=message,
    )
    return _decode_signature(response)


def _resolve_typed_data_hashes(
    *,
    payload_hex: str,
    domain_separator_hex: str,
    hash_struct_message_hex: str,
) -> tuple[bytes, bytes]:
    # Preferred shape: explicit EIP-712 hashes.
    if domain_separator_hex or hash_struct_message_hex:
        if not domain_separator_hex or not hash_struct_message_hex:
            raise ValueError(
                "typed_data requires both inputs.domain_separator_hex and "
                "inputs.hash_struct_message_hex"
            )
        domain_separator = _parse_fixed_hex(
            domain_separator_hex, name="domain_separator_hex", size=32
        )
        hash_struct_message = _parse_fixed_hex(
            hash_struct_message_hex, name="hash_struct_message_hex", size=32
        )
        return domain_separator, hash_struct_message

    # Backward-compatible fallback: payload_hex is a concatenated 64-byte blob
    # [domainSeparator(32) || hashStruct(message)(32)].
    if payload_hex:
        combined = _parse_fixed_hex(payload_hex, name="typed_data payload_hex", size=64)
        return combined[:32], combined[32:]

    raise ValueError(
        "typed_data requires either both inputs.domain_separator_hex + "
        "inputs.hash_struct_message_hex, or inputs.payload_hex as 64-byte "
        "combined hash data."
    )


def _sign_typed_data(
    *,
    dongle: Any,
    derivation_path: str,
    payload_hex: str,
    domain_separator_hex: str,
    hash_struct_message_hex: str,
) -> dict[str, Any]:
    path_bytes = _encode_bip32_path(derivation_path)
    domain_separator, hash_struct_message = _resolve_typed_data_hashes(
        payload_hex=payload_hex,
        domain_separator_hex=domain_separator_hex,
        hash_struct_message_hex=hash_struct_message_hex,
    )
    payload = path_bytes + domain_separator + hash_struct_message
    response = dongle.exchange(
        _apdu(ETH_CLA, INS_SIGN_EIP712_HASHED_MESSAGE, 0x00, 0x00, payload)
    )
    return _decode_signature_vrs(response)


def _execute_hid_sign(inputs: dict[str, Any]) -> dict[str, Any]:
    payload_kind = str(inputs.get("payload_kind", "transaction"))
    payload_hex = str(inputs.get("payload_hex", "")).strip()
    domain_separator_hex = str(inputs.get("domain_separator_hex", "")).strip()
    hash_struct_message_hex = str(inputs.get("hash_struct_message_hex", "")).strip()
    derivation_path = str(inputs.get("derivation_path", "44'/60'/0'/0/0"))

    if payload_kind in ("transaction", "message") and not payload_hex:
        raise ValueError("inputs.payload_hex is required for HID signing")

    try:
        from ledgerblue.comm import getDongle
    except ImportError as exc:
        raise RuntimeError(
            "Missing Ledger HID dependency. Install with: pip install ledgerblue hidapi"
        ) from exc

    dongle = getDongle(debug=False)
    try:
        if payload_kind == "transaction":
            signature = _sign_transaction(
                dongle=dongle,
                derivation_path=derivation_path,
                payload_hex=payload_hex,
            )
        elif payload_kind == "message":
            signature = _sign_message(
                dongle=dongle,
                derivation_path=derivation_path,
                payload_hex=payload_hex,
            )
        elif payload_kind == "typed_data":
            signature = _sign_typed_data(
                dongle=dongle,
                derivation_path=derivation_path,
                payload_hex=payload_hex,
                domain_separator_hex=domain_separator_hex,
                hash_struct_message_hex=hash_struct_message_hex,
            )
        else:
            raise ValueError(
                f"unsupported payload_kind={payload_kind!r}. "
                "Expected one of: transaction, message, typed_data."
            )
    finally:
        try:
            dongle.close()
        except Exception:
            pass

    return {
        "status": "signed",
        "payload_kind": payload_kind,
        "derivation_path": derivation_path,
        "signature": signature,
    }


def run_once(config: dict, dry_run: bool, execute: bool) -> dict:
    inputs = config.get("inputs", {})
    if not execute:
        return {
            "status": "ok",
            "dry_run": dry_run,
            "connectors": AVAILABLE_CONNECTORS,
            "input_keys": sorted(inputs.keys()),
            "note": "Pass --execute to run USB/HID signing against a connected Ledger.",
        }

    if dry_run:
        raise RuntimeError("Refusing to sign while dry_run=true. Set dry_run=false in config.")

    return _execute_hid_sign(inputs)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run, execute=args.execute)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
