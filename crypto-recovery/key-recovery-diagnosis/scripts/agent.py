#!/usr/bin/env python3
"""Local diagnostic runner for the key-recovery-diagnosis skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_DRY_RUN = True
LOSS_TYPE_CHOICES = {
    "password": "Wallet password",
    "seed": "Seed phrase / mnemonic",
    "passphrase": "BIP39 passphrase",
    "hardware pin": "Hardware wallet PIN",
    "hardware": "Hardware wallet PIN",
    "exchange": "Exchange account access",
    "unsure": "Unsure / multiple things",
}
VALUE_CHOICES = {
    "under $1,000": "Under $1,000",
    "$1,000 - $10,000": "$1,000 - $10,000",
    "$10,000 - $100,000": "$10,000 - $100,000",
    "over $100,000": "Over $100,000",
    "prefer not to say": "Prefer not to say",
}
SCAM_RED_FLAGS = (
    "upfront fees",
    "asking for seed phrase, private key, password, or wallet file",
    "unsolicited outreach",
)
BTCR_RECOVER_CLONE = "git clone https://github.com/3rdIteration/btcrecover.git"
INLINE_SECRET_KEYS = {
    "mnemonic",
    "seed",
    "seed_phrase",
    "password",
    "passphrase",
    "tokenlist_inline",
    "passwordlist_inline",
    "seedlist_inline",
}


@dataclass
class ScamCheck:
    shared_with_service: bool
    upfront_fee: bool = False
    asked_for_credentials: bool = False
    unsolicited_contact: bool = False

    @property
    def exposed(self) -> bool:
        return self.upfront_fee or self.asked_for_credentials or self.unsolicited_contact


@dataclass
class Answers:
    loss_type: str
    wallet_type: str
    knowledge_summary: str
    known_identifier: str
    value_range: str
    attempted_steps: str
    scam_check: ScamCheck


@dataclass
class Diagnosis:
    scenario_id: int
    scenario_name: str
    feasibility: str
    explanation: str
    next_steps: list[str]
    diy_commands: list[str]
    recommended_path: str
    honest_assessment: str


class BtcrecoverSafetyError(Exception):
    """Raised when a btcrecover execution request violates safety policy."""


class HashcatSafetyError(Exception):
    """Raised when a hashcat execution request violates safety policy."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the key recovery diagnosis flow.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config file. Defaults to config.json.",
    )
    parser.add_argument(
        "--answers-file",
        help="Optional JSON file containing the questionnaire answers.",
    )
    parser.add_argument(
        "--report-out",
        help="Optional path to write the non-sensitive diagnostic report JSON.",
    )
    parser.add_argument(
        "--send-report",
        action="store_true",
        help="Emit consent-gated instructions for sending the diagnostic report to the sponsor intake email.",
    )
    parser.add_argument(
        "--run-btcrecover",
        action="store_true",
        help="Run a local btcrecover subprocess for eligible DIY cases using file-based inputs from config.",
    )
    parser.add_argument(
        "--run-hashcat",
        action="store_true",
        help="Run a local hashcat subprocess for eligible wallet-password cases using file-based inputs from config.",
    )
    return parser.parse_args()


def load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return json.loads(candidate.read_text(encoding="utf-8"))


def load_config(config_path: str) -> dict[str, Any]:
    config = load_json(config_path)
    if config:
        return config
    example_path = Path("config.example.json")
    if example_path.exists():
        return json.loads(example_path.read_text(encoding="utf-8"))
    return {}


def env_or_config(config: dict[str, Any], path: list[str], env_name: str, default: str = "") -> str:
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return str(current).strip() if current is not None else default


def bool_from_config(config: dict[str, Any], path: list[str], default: bool = False) -> bool:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return bool(current)


def ask(prompt: str) -> str:
    return input(f"{prompt}\n> ").strip()


def choose(prompt: str, options: dict[str, str]) -> str:
    print(prompt)
    for key, label in options.items():
        print(f"- {key}: {label}")
    while True:
        answer = input("> ").strip().lower()
        if answer in options:
            return answer
        print("Choose one of the listed options.")


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/n]\n> ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Answer y or n.")


def collect_answers_interactive() -> Answers:
    print(
        "Safety notice: do not paste a seed phrase, private key, full wallet file, or password here. "
        "This flow only asks for high-level recovery facts."
    )
    loss_type = choose(
        "Q1: What type of access did you lose?",
        LOSS_TYPE_CHOICES,
    )
    if loss_type == "exchange":
        print("This is not a key-recovery issue. The right next step is the exchange's official account recovery flow.")
        return Answers(
            loss_type=loss_type,
            wallet_type="exchange",
            knowledge_summary="Exchange login or account access issue",
            known_identifier="",
            value_range="Prefer not to say",
            attempted_steps="",
            scam_check=ScamCheck(shared_with_service=False),
        )

    wallet_type = ask("Q2: Which wallet or storage type?")
    q3_prompt = knowledge_prompt(loss_type)
    knowledge_summary = ask(f"Q3: {q3_prompt}")
    known_identifier = ask(
        "Q4: Do you have a known receiving address or transaction ID? "
        "You can answer yes/no or provide a masked identifier."
    )
    value_range = choose("Q5: Approximate value at stake?", VALUE_CHOICES)
    attempted_steps = ask("Q6: Have you already tried any recovery steps?")
    scam_check = collect_scam_check()

    return Answers(
        loss_type=loss_type,
        wallet_type=wallet_type,
        knowledge_summary=knowledge_summary,
        known_identifier=known_identifier,
        value_range=value_range,
        attempted_steps=attempted_steps,
        scam_check=scam_check,
    )


def collect_scam_check() -> ScamCheck:
    shared = ask_yes_no('Q7: Have you shared your situation with any "recovery service" already?')
    if not shared:
        return ScamCheck(shared_with_service=False)
    return ScamCheck(
        shared_with_service=True,
        upfront_fee=ask_yes_no("Did they ask for upfront fees?"),
        asked_for_credentials=ask_yes_no(
            "Did they ask for your seed phrase, private key, password, or wallet file?"
        ),
        unsolicited_contact=ask_yes_no("Did they contact you first?"),
    )


def knowledge_prompt(loss_type: str) -> str:
    prompts = {
        "password": (
            "Do you remember any part of the password, its approximate length, character types, "
            "or common variations? Do you still have the wallet file?"
        ),
        "seed": (
            "How many of the 12 or 24 words do you still have? Are any uncertain or illegible? "
            "Is the word order known?"
        ),
        "passphrase": "Do you still have the base seed phrase, and is the missing piece only the passphrase?",
        "hardware pin": (
            "How many PIN attempts remain, is the device physically intact, and do you still have the recovery seed?"
        ),
        "hardware": (
            "How many PIN attempts remain, is the device physically intact, and do you still have the recovery seed?"
        ),
        "unsure": "What facts do you still know, even if they feel incomplete or vague?",
    }
    return prompts.get(loss_type, prompts["unsure"])


def answers_from_json(payload: dict[str, Any]) -> Answers:
    scam_payload = payload.get("scam_check", {})
    if not isinstance(scam_payload, dict):
        scam_payload = {}
    return Answers(
        loss_type=str(payload.get("loss_type", "unsure")).strip().lower() or "unsure",
        wallet_type=str(payload.get("wallet_type", "")).strip(),
        knowledge_summary=str(payload.get("knowledge_summary", "")).strip(),
        known_identifier=str(payload.get("known_identifier", "")).strip(),
        value_range=str(payload.get("value_range", "Prefer not to say")).strip() or "Prefer not to say",
        attempted_steps=str(payload.get("attempted_steps", "")).strip(),
        scam_check=ScamCheck(
            shared_with_service=bool(scam_payload.get("shared_with_service", False)),
            upfront_fee=bool(scam_payload.get("upfront_fee", False)),
            asked_for_credentials=bool(scam_payload.get("asked_for_credentials", False)),
            unsolicited_contact=bool(scam_payload.get("unsolicited_contact", False)),
        ),
    )


def normalize_loss_type(raw: str) -> str:
    value = raw.strip().lower()
    if value in LOSS_TYPE_CHOICES:
        return value
    if "exchange" in value:
        return "exchange"
    if "passphrase" in value:
        return "passphrase"
    if "password" in value:
        return "password"
    if "seed" in value or "mnemonic" in value:
        return "seed"
    if "pin" in value or "ledger" in value or "trezor" in value or "hardware" in value:
        return "hardware pin"
    return "unsure"


def parse_missing_words(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:missing|unknown|lost)", text.lower())
    if match:
        return int(match.group(1))
    match = re.search(r"missing\s*(\d+)", text.lower())
    if match:
        return int(match.group(1))
    return None


def has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def has_wallet_file(answers: Answers) -> bool:
    return has_phrase(
        answers.knowledge_summary,
        ("wallet file", "i have the file", "file exists", "still have the wallet", "wallet.dat"),
    ) or has_phrase(answers.attempted_steps, ("wallet file", "wallet.dat"))


def has_seed_backup(answers: Answers) -> bool:
    return has_phrase(
        answers.knowledge_summary,
        ("have the recovery seed", "have the seed", "seed exists", "seed backup"),
    )


def known_identifier_available(answers: Answers) -> bool:
    lowered = answers.known_identifier.lower()
    return bool(lowered) and lowered not in {"no", "none", "unknown", "not sure"}


def likely_technical_user(answers: Answers, config: dict[str, Any]) -> bool:
    if bool(config.get("inputs", {}).get("technical_mode", False)):
        return True
    clues = (
        "btcrecover",
        "hashcat",
        "python",
        "terminal",
        "command line",
        "gpu",
        "wordlist",
    )
    return has_phrase(answers.attempted_steps, clues) or has_phrase(answers.knowledge_summary, clues)


def effective_path(diagnosis: Diagnosis, technical_user: bool) -> str:
    if diagnosis.recommended_path == "redirect":
        return "redirect"
    if technical_user:
        return diagnosis.recommended_path
    return "handoff"


def classify(answers: Answers) -> Diagnosis:
    loss_type = normalize_loss_type(answers.loss_type)
    knowledge = answers.knowledge_summary.lower()
    wallet_type = answers.wallet_type or "unspecified wallet"
    identifier_note = (
        "A known address or transaction ID is available for verification."
        if known_identifier_available(answers)
        else "No known address or transaction ID was provided, which reduces verification confidence."
    )

    if loss_type == "exchange":
        return Diagnosis(
            scenario_id=7,
            scenario_name="Exchange account access issue",
            feasibility="Redirect",
            explanation="This is not a key-recovery problem. It is an exchange account-access problem.",
            next_steps=[
                "Use the exchange's official account recovery and support channels.",
                "Do not pay any third-party recovery service.",
                "Preserve any account ownership evidence, support ticket IDs, and KYC history.",
            ],
            diy_commands=[],
            recommended_path="redirect",
            honest_assessment="The right path here is exchange support, not cryptographic key recovery.",
        )

    if loss_type == "password" and has_wallet_file(answers):
        return Diagnosis(
            scenario_id=1,
            scenario_name="Wallet password lost, file exists",
            feasibility="DIY-easy",
            explanation=(
                f"You described a password-recovery case for {wallet_type} and indicated the wallet file still exists. "
                f"{identifier_note}"
            ),
            next_steps=[
                "Use btcrecover first with a token list based on remembered fragments and patterns.",
                "Use hashcat only if the password space is still bounded and GPU acceleration is justified.",
                "Keep all work local and offline where practical.",
            ],
            diy_commands=password_commands(),
            recommended_path="diy",
            honest_assessment="This is often recoverable if your password memory is still structured rather than random.",
        )

    if loss_type == "seed":
        missing_words = parse_missing_words(knowledge)
        order_unknown = has_phrase(knowledge, ("order unknown", "wrong order", "scrambled", "mixed up"))
        all_words_present = has_phrase(knowledge, ("all 12", "all 24", "all words", "every word"))
        if missing_words is not None and 1 <= missing_words <= 3:
            return Diagnosis(
                scenario_id=2,
                scenario_name="Partial seed phrase, 1 to 3 words missing",
                feasibility="DIY-moderate",
                explanation=(
                    f"You appear to have a partial seed for {wallet_type} with only a small number of words missing. "
                    f"{identifier_note}"
                ),
                next_steps=[
                    "Use btcrecover seed recovery with the known words and unknown positions.",
                    "Prefer a known receiving address for verification.",
                    "Expect search time to rise sharply as missing words increase from 1 to 3.",
                ],
                diy_commands=partial_seed_commands(),
                recommended_path="diy",
                honest_assessment="This is still within realistic self-recovery range if the missing-word count is truly small.",
            )
        if order_unknown and all_words_present:
            return Diagnosis(
                scenario_id=3,
                scenario_name="Seed order unknown",
                feasibility="DIY-hard",
                explanation=(
                    f"You likely have all seed words for {wallet_type}, but not the correct ordering. "
                    f"{identifier_note}"
                ),
                next_steps=[
                    "Use btcrecover only if you can narrow the permutation space materially.",
                    "Try grouping words by remembered adjacency before any brute-force attempt.",
                    "Escalate if the permutation search remains too large.",
                ],
                diy_commands=seed_order_commands(),
                recommended_path="diy",
                honest_assessment="This is materially harder than missing one or two words because permutation count explodes.",
            )
        if missing_words is not None and missing_words > 3:
            return Diagnosis(
                scenario_id=8,
                scenario_name="Likely unrecoverable",
                feasibility="Honest assessment",
                explanation=(
                    f"The current seed detail for {wallet_type} suggests more than 3 words are missing or uncertain. "
                    f"{identifier_note}"
                ),
                next_steps=[
                    "Do not spend money on recovery promises.",
                    "If you want a final feasibility read, use the sponsor handoff summary only.",
                ],
                diy_commands=[],
                recommended_path="handoff",
                honest_assessment="Once too many seed words are missing, search cost usually becomes impractical.",
            )

    if loss_type == "passphrase":
        return Diagnosis(
            scenario_id=4,
            scenario_name="BIP39 passphrase forgotten",
            feasibility="DIY-hard to expert",
            explanation=(
                f"You still appear to have the base seed for {wallet_type}, but the missing layer is the passphrase. "
                f"{identifier_note}"
            ),
            next_steps=[
                "Short, pattern-based passphrases may still be worth a local btcrecover attempt.",
                "Long or context-heavy passphrases usually need an expert-designed search strategy.",
                "Do not confuse a seed word with the optional BIP39 passphrase.",
            ],
            diy_commands=passphrase_commands(),
            recommended_path="hybrid",
            honest_assessment="These cases range from solvable to effectively impossible depending on how structured the passphrase memory is.",
        )

    if loss_type == "hardware pin":
        if not has_seed_backup(answers):
            return Diagnosis(
                scenario_id=5,
                scenario_name="Hardware wallet PIN lockout and seed lost",
                feasibility="Expert-only",
                explanation=(
                    f"This looks like a hardware-device lockout for {wallet_type} without a usable recovery seed backup."
                ),
                next_steps=[
                    "Do not keep guessing if the device is near wipe thresholds.",
                    "Move this to expert review with a structured diagnostic summary.",
                    "Keep the device powered down and documented."
                ],
                diy_commands=[],
                recommended_path="handoff",
                honest_assessment="This is not a casual DIY case. The risk of device wipe or irreversible lockout is too high.",
            )
        return Diagnosis(
            scenario_id=5,
            scenario_name="Hardware wallet PIN issue with seed backup available",
            feasibility="Expert-only",
            explanation=(
                f"You still have a recovery seed for {wallet_type}. The safer path is wallet restoration, not PIN cracking."
            ),
            next_steps=[
                "Restore from the seed onto a clean device or software wallet you trust.",
                "Do not continue PIN attempts unless you fully understand the device's wipe behavior.",
                "Use sponsor review if you are unsure how to restore safely."
            ],
            diy_commands=[],
            recommended_path="handoff",
            honest_assessment="The presence of a seed backup changes the path, but the device itself should still be treated cautiously.",
        )

    if loss_type == "unsure" or has_phrase(knowledge, ("vague", "not sure", "don't remember", "unclear")):
        return Diagnosis(
            scenario_id=6,
            scenario_name="Total loss, vague memories only",
            feasibility="Expert-only",
            explanation=(
                f"The current facts for {wallet_type} are too incomplete for a safe self-recovery plan. "
                f"{identifier_note}"
            ),
            next_steps=[
                "Create a sponsor-safe diagnostic summary.",
                "Collect any old addresses, screenshots, backups, purchase records, or device details before a review.",
                "Avoid random brute-force attempts that you cannot verify."
            ],
            diy_commands=[],
            recommended_path="handoff",
            honest_assessment="Without concrete recovery facts, the first task is evidence gathering, not tool execution.",
        )

    return Diagnosis(
        scenario_id=8,
        scenario_name="Likely unrecoverable",
        feasibility="Honest assessment",
        explanation=(
            f"The known facts for {wallet_type} do not map to a safe or realistic self-recovery path. "
            f"{identifier_note}"
        ),
        next_steps=[
            "Do not pay anyone based on certainty claims.",
            "Use the sponsor handoff only if you want a final feasibility check.",
        ],
        diy_commands=[],
        recommended_path="handoff",
        honest_assessment="Some cases are not practically recoverable, and the honest outcome matters more than false hope.",
    )


def password_commands() -> list[str]:
    return [
        BTCR_RECOVER_CLONE,
        "cd btcrecover && python3 -m pip install -r requirements.txt",
        "cd btcrecover && python3 run-all-tests.py -vv",
        "cd btcrecover && python3 btcrecover.py --wallet <wallet_file> --tokenlist tokens.txt",
        "hashcat -m <wallet_hash_mode> <wallet_hash.txt> <wordlist_or_rules>",
    ]


def partial_seed_commands() -> list[str]:
    return [
        BTCR_RECOVER_CLONE,
        "cd btcrecover && python3 -m pip install -r requirements.txt",
        'cd btcrecover && python3 seedrecover.py --wallet-type <wallet_type> --addr <known_address> --mnemonic "word1 word2 ... %d ..."',
    ]


def seed_order_commands() -> list[str]:
    return [
        BTCR_RECOVER_CLONE,
        "cd btcrecover && python3 -m pip install -r requirements.txt",
        'cd btcrecover && python3 seedrecover.py --wallet-type <wallet_type> --addr <known_address> --mnemonic "word1 word2 ... word12" --typos-swap',
    ]


def passphrase_commands() -> list[str]:
    return [
        BTCR_RECOVER_CLONE,
        "cd btcrecover && python3 -m pip install -r requirements.txt",
        'cd btcrecover && python3 seedrecover.py --wallet-type <wallet_type> --addr <known_address> --mnemonic "<base seed>" --passphrase-list passphrases.txt',
    ]


def resolve_existing_file(path_str: str, *, label: str) -> Path:
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if not candidate.exists():
        raise BtcrecoverSafetyError(f"{label} does not exist: {candidate}")
    if not candidate.is_file():
        raise BtcrecoverSafetyError(f"{label} must be a file: {candidate}")
    return candidate


def resolve_existing_dir(path_str: str, *, label: str) -> Path:
    candidate = Path(path_str).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if not candidate.exists():
        raise BtcrecoverSafetyError(f"{label} does not exist: {candidate}")
    if not candidate.is_dir():
        raise BtcrecoverSafetyError(f"{label} must be a directory: {candidate}")
    return candidate


def validate_no_inline_secrets(request: dict[str, Any]) -> None:
    for key in INLINE_SECRET_KEYS:
        value = request.get(key)
        if value not in (None, "", False):
            raise BtcrecoverSafetyError(
                f"Inline secret material is not allowed in btcrecover request: {key}"
            )


def validate_no_inline_secrets_hashcat(request: dict[str, Any]) -> None:
    for key in INLINE_SECRET_KEYS:
        value = request.get(key)
        if value not in (None, "", False):
            raise HashcatSafetyError(
                f"Inline secret material is not allowed in hashcat request: {key}"
            )


def ensure_btcrecover_allowed(
    *,
    config: dict[str, Any],
    diagnosis: Diagnosis,
    technical_user: bool,
) -> None:
    if not technical_user:
        raise BtcrecoverSafetyError(
            "Local btcrecover execution is only allowed for technical users."
        )
    if diagnosis.recommended_path not in {"diy", "hybrid"}:
        raise BtcrecoverSafetyError(
            f"Diagnosis path '{diagnosis.recommended_path}' is not eligible for local btcrecover execution."
        )
    if not bool_from_config(config, ["inputs", "technical_mode"], False):
        raise BtcrecoverSafetyError(
            "config.inputs.technical_mode must be true before local btcrecover execution is allowed."
        )
    if not bool_from_config(config, ["inputs", "allow_local_btcrecover"], False):
        raise BtcrecoverSafetyError(
            "config.inputs.allow_local_btcrecover must be true before local btcrecover execution is allowed."
        )
    if not bool_from_config(config, ["inputs", "user_confirmed_understands_risk"], False):
        raise BtcrecoverSafetyError(
            "config.inputs.user_confirmed_understands_risk must be true before local btcrecover execution is allowed."
        )


def btcrecover_request(config: dict[str, Any]) -> dict[str, Any]:
    request = config.get("btcrecover", {}).get("request", {})
    if not isinstance(request, dict):
        raise BtcrecoverSafetyError("config.btcrecover.request must be an object.")
    validate_no_inline_secrets(request)
    return request


def btcrecover_repo_and_python(config: dict[str, Any]) -> tuple[Path, str]:
    repo_path = str(config.get("btcrecover", {}).get("repo_path", "")).strip()
    python_bin = str(config.get("btcrecover", {}).get("python_bin", "python3")).strip() or "python3"
    if not repo_path:
        raise BtcrecoverSafetyError("config.btcrecover.repo_path must point to a local btcrecover checkout.")
    repo = resolve_existing_dir(repo_path, label="btcrecover repo_path")
    return repo, python_bin


def build_btcrecover_command(config: dict[str, Any], diagnosis: Diagnosis) -> tuple[list[str], Path]:
    request = btcrecover_request(config)
    repo, python_bin = btcrecover_repo_and_python(config)
    mode = str(request.get("mode", "")).strip().lower()

    if mode == "wallet-password":
        wallet_file = resolve_existing_file(str(request.get("wallet_file", "")), label="wallet_file")
        tokenlist = str(request.get("tokenlist_file", "")).strip()
        passwordlist = str(request.get("passwordlist_file", "")).strip()
        if not tokenlist and not passwordlist:
            raise BtcrecoverSafetyError(
                "wallet-password mode requires either tokenlist_file or passwordlist_file."
            )
        command = [python_bin, str(repo / "btcrecover.py"), "--wallet", str(wallet_file)]
        if tokenlist:
            command.extend(["--tokenlist", str(resolve_existing_file(tokenlist, label="tokenlist_file"))])
        if passwordlist:
            command.extend(["--passwordlist", str(resolve_existing_file(passwordlist, label="passwordlist_file"))])
        return command, repo

    if mode == "partial-seed":
        wallet_type = str(request.get("wallet_type", "")).strip().lower()
        addrs = str(request.get("addrs", "")).strip()
        tokenlist_file = str(request.get("tokenlist_file", "")).strip()
        seedlist_file = str(request.get("seedlist_file", "")).strip()
        mnemonic_length = int(request.get("mnemonic_length", 12))
        addr_limit = int(request.get("addr_limit", 5))
        language = str(request.get("language", "")).strip()
        if diagnosis.scenario_id != 2:
            raise BtcrecoverSafetyError(
                "partial-seed btcrecover execution is only allowed for scenario 2."
            )
        if not wallet_type:
            raise BtcrecoverSafetyError("partial-seed mode requires wallet_type.")
        if not addrs:
            raise BtcrecoverSafetyError("partial-seed mode requires addrs for verification.")
        if not tokenlist_file and not seedlist_file:
            raise BtcrecoverSafetyError(
                "partial-seed mode requires either tokenlist_file or seedlist_file."
            )
        command = [
            python_bin,
            str(repo / "seedrecover.py"),
            "--wallet-type",
            wallet_type,
            "--addrs",
            addrs,
            "--addr-limit",
            str(addr_limit),
            "--mnemonic-length",
            str(mnemonic_length),
        ]
        if language:
            command.extend(["--language", language])
        if tokenlist_file:
            command.extend(["--tokenlist", str(resolve_existing_file(tokenlist_file, label="tokenlist_file"))])
        if seedlist_file:
            command.extend(["--seedlist", str(resolve_existing_file(seedlist_file, label="seedlist_file"))])
        return command, repo

    raise BtcrecoverSafetyError(
        "Only btcrecover modes 'wallet-password' and 'partial-seed' are executable in this runtime."
    )


def run_btcrecover(
    *,
    config: dict[str, Any],
    diagnosis: Diagnosis,
    technical_user: bool,
) -> dict[str, Any]:
    ensure_btcrecover_allowed(config=config, diagnosis=diagnosis, technical_user=technical_user)
    command, repo = build_btcrecover_command(config, diagnosis)
    timeout_seconds = int(config.get("btcrecover", {}).get("timeout_seconds", 300))
    result = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "tool": "btcrecover",
        "command": command,
        "cwd": str(repo),
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def ensure_hashcat_allowed(
    *,
    config: dict[str, Any],
    diagnosis: Diagnosis,
    technical_user: bool,
) -> None:
    if not technical_user:
        raise HashcatSafetyError(
            "Local hashcat execution is only allowed for technical users."
        )
    if diagnosis.scenario_id != 1:
        raise HashcatSafetyError(
            "Local hashcat execution is only allowed for scenario 1 wallet-password cases."
        )
    if not bool_from_config(config, ["inputs", "technical_mode"], False):
        raise HashcatSafetyError(
            "config.inputs.technical_mode must be true before local hashcat execution is allowed."
        )
    if not bool_from_config(config, ["inputs", "allow_local_hashcat"], False):
        raise HashcatSafetyError(
            "config.inputs.allow_local_hashcat must be true before local hashcat execution is allowed."
        )
    if not bool_from_config(config, ["inputs", "user_confirmed_understands_risk"], False):
        raise HashcatSafetyError(
            "config.inputs.user_confirmed_understands_risk must be true before local hashcat execution is allowed."
        )


def hashcat_request(config: dict[str, Any]) -> dict[str, Any]:
    request = config.get("hashcat", {}).get("request", {})
    if not isinstance(request, dict):
        raise HashcatSafetyError("config.hashcat.request must be an object.")
    validate_no_inline_secrets_hashcat(request)
    return request


def hashcat_binary(config: dict[str, Any]) -> str:
    binary_path = str(config.get("hashcat", {}).get("binary_path", "hashcat")).strip()
    return binary_path or "hashcat"


def build_hashcat_command(config: dict[str, Any], diagnosis: Diagnosis) -> tuple[list[str], Path]:
    request = hashcat_request(config)
    if diagnosis.scenario_id != 1:
        raise HashcatSafetyError(
            "Only scenario 1 wallet-password cases are eligible for local hashcat execution."
        )

    mode = str(request.get("mode", "wallet-password")).strip().lower()
    if mode != "wallet-password":
        raise HashcatSafetyError(
            "Only hashcat mode 'wallet-password' is executable in this runtime."
        )

    hash_mode = str(request.get("hash_mode", "")).strip()
    if not hash_mode or not hash_mode.isdigit():
        raise HashcatSafetyError("hashcat wallet-password mode requires a numeric hash_mode.")

    hash_file = resolve_existing_file(str(request.get("hash_file", "")), label="hash_file")
    attack_mode = int(request.get("attack_mode", 0))
    if attack_mode not in {0, 3}:
        raise HashcatSafetyError("Only hashcat attack_mode 0 or 3 is allowed in this runtime.")

    binary = hashcat_binary(config)
    command = [
        binary,
        "-m",
        hash_mode,
        "-a",
        str(attack_mode),
    ]

    session = str(request.get("session", "")).strip()
    if session:
        command.extend(["--session", session])

    extra_args = request.get("extra_args", [])
    if extra_args not in (None, []):
        raise HashcatSafetyError("Arbitrary extra_args are not allowed for local hashcat execution.")

    command.append(str(hash_file))
    cwd = hash_file.parent

    if attack_mode == 0:
        wordlist = str(request.get("wordlist_file", "")).strip()
        if not wordlist:
            raise HashcatSafetyError("hashcat attack_mode 0 requires wordlist_file.")
        command.append(str(resolve_existing_file(wordlist, label="wordlist_file")))
        rule_file = str(request.get("rule_file", "")).strip()
        if rule_file:
            command.extend(["-r", str(resolve_existing_file(rule_file, label="rule_file"))])
    else:
        mask = str(request.get("mask", "")).strip()
        if not mask:
            raise HashcatSafetyError("hashcat attack_mode 3 requires mask.")
        command.append(mask)

    return command, cwd


def run_hashcat(
    *,
    config: dict[str, Any],
    diagnosis: Diagnosis,
    technical_user: bool,
) -> dict[str, Any]:
    ensure_hashcat_allowed(config=config, diagnosis=diagnosis, technical_user=technical_user)
    command, cwd = build_hashcat_command(config, diagnosis)
    timeout_seconds = int(config.get("hashcat", {}).get("timeout_seconds", 300))
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "tool": "hashcat",
        "command": command,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def mask_identifier(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) <= 10:
        return cleaned
    return f"{cleaned[:6]}...{cleaned[-4:]}"


def build_report(answers: Answers, diagnosis: Diagnosis, technical_user: bool) -> dict[str, Any]:
    routed_path = effective_path(diagnosis, technical_user)
    return {
        "scenario_id": diagnosis.scenario_id,
        "scenario_name": diagnosis.scenario_name,
        "feasibility": diagnosis.feasibility,
        "wallet_type": answers.wallet_type,
        "loss_type": normalize_loss_type(answers.loss_type),
        "what_is_known": answers.knowledge_summary,
        "known_identifier_available": known_identifier_available(answers),
        "known_identifier_masked": mask_identifier(answers.known_identifier) if known_identifier_available(answers) else "",
        "value_range": answers.value_range,
        "attempted_steps": answers.attempted_steps,
        "technical_user": technical_user,
        "recommended_path": routed_path,
        "scam_exposure": {
            "shared_with_service": answers.scam_check.shared_with_service,
            "upfront_fee": answers.scam_check.upfront_fee,
            "asked_for_credentials": answers.scam_check.asked_for_credentials,
            "unsolicited_contact": answers.scam_check.unsolicited_contact,
            "exposed": answers.scam_check.exposed,
        },
    }


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Key Recovery Diagnostic Summary",
        "",
        f"- Scenario: {report['scenario_id']} - {report['scenario_name']}",
        f"- Feasibility: {report['feasibility']}",
        f"- Wallet type: {report['wallet_type'] or 'unspecified'}",
        f"- Loss type: {report['loss_type']}",
        f"- Known vs missing: {report['what_is_known']}",
        f"- Known identifier available: {report['known_identifier_available']}",
        f"- Known identifier masked: {report['known_identifier_masked'] or 'none provided'}",
        f"- Value range: {report['value_range']}",
        f"- Prior attempts: {report['attempted_steps'] or 'none recorded'}",
        f"- Technical user: {report['technical_user']}",
        f"- Recommended path: {report['recommended_path']}",
        "",
        "## Scam Exposure",
        "",
        f"- Shared with service: {report['scam_exposure']['shared_with_service']}",
        f"- Upfront fee request: {report['scam_exposure']['upfront_fee']}",
        f"- Asked for credentials: {report['scam_exposure']['asked_for_credentials']}",
        f"- Unsolicited contact: {report['scam_exposure']['unsolicited_contact']}",
        f"- Exposure flagged: {report['scam_exposure']['exposed']}",
    ]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], path: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def maybe_send_report(
    *,
    report: dict[str, Any],
    report_markdown: str,
    config: dict[str, Any],
    send_report: bool,
) -> dict[str, Any]:
    del report_markdown
    share_report = bool(config.get("inputs", {}).get("share_report", False))
    if not send_report:
        return {"status": "skipped", "reason": "send_report flag not set"}
    if not share_report:
        return {"status": "blocked", "reason": "share_report consent is false"}
    intake_email = env_or_config(config, ["sponsor", "intake_email"], "SPONSOR_INTAKE_EMAIL", "hello@serendb.com")
    scenario_slug = str(report.get("scenario_name", "diagnosis")).lower()
    scenario_slug = re.sub(r"[^a-z0-9]+", "-", scenario_slug).strip("-")
    return {
        "status": "instructions",
        "channel": "manual_email",
        "to": intake_email,
        "subject": f"Key recovery diagnostic summary - {scenario_slug or 'case'}",
        "message": f"Send the generated analysis file to {intake_email}. Seren will forward it to Tom for follow-up.",
        "forwarding_note": "Seren forwards sponsor intake emails to Tom.",
    }


def anti_scam_lines() -> list[str]:
    return [
        "Anti-scam warning:",
        "1. Upfront fees are a red flag.",
        "2. Anyone asking for your seed phrase, private key, password, or wallet file is a red flag.",
        "3. Unsolicited outreach from a recovery service is a red flag.",
        "4. Second-order scams are common after an initial recovery scam.",
    ]


def disclaimer_lines() -> list[str]:
    return [
        "Disclaimer:",
        "1. This skill is software guidance only and not legal, financial, tax, cybersecurity, or forensic advice.",
        "2. No recovery outcome is guaranteed, and some wallets are unrecoverable.",
        "3. Local recovery attempts can worsen the situation, including lockout, device wipe, or corrupted files.",
        "4. Never share a seed phrase, private key, password, passphrase, or full wallet file in chat or with the sponsor.",
        "5. Sponsor handoff is an introduction only and not a promise of recovery, pricing, or engagement.",
        "6. This skill is provided as-is, and you are responsible for any local commands you choose to run.",
    ]


def print_result(
    *,
    answers: Answers,
    diagnosis: Diagnosis,
    technical_user: bool,
    config: dict[str, Any],
    send_result: dict[str, Any],
    btcrecover_result: dict[str, Any] | None,
    hashcat_result: dict[str, Any] | None,
    report_path: str | None,
) -> None:
    sponsor_booking_url = env_or_config(config, ["sponsor", "booking_url"], "SPONSOR_BOOKING_URL", "SPONSOR_BOOKING_URL")
    sponsor_intake_email = env_or_config(config, ["sponsor", "intake_email"], "SPONSOR_INTAKE_EMAIL", "SPONSOR_INTAKE_EMAIL")

    routed_path = effective_path(diagnosis, technical_user)
    print(json.dumps(
        {
            "scenario_id": diagnosis.scenario_id,
            "scenario_name": diagnosis.scenario_name,
            "feasibility": diagnosis.feasibility,
            "technical_user": technical_user,
            "recommended_path": routed_path,
            "report_path": report_path,
            "send_result": send_result,
            "btcrecover_result": btcrecover_result,
            "hashcat_result": hashcat_result,
        },
        indent=2,
    ))
    print()
    print(f"Diagnosis: {diagnosis.scenario_name}")
    print(f"Feasibility: {diagnosis.feasibility}")
    print(diagnosis.explanation)
    print()
    print("Next steps:")
    for step in diagnosis.next_steps:
        print(f"- {step}")
    if technical_user and diagnosis.diy_commands:
        print()
        print("Local command templates:")
        for command in diagnosis.diy_commands:
            print(f"- {command}")
    elif diagnosis.diy_commands:
        print()
        print("Routing note: this scenario is technically DIY-feasible, but sponsor handoff is the default because technical mode is off.")
    print()
    print(f"Assessment: {diagnosis.honest_assessment}")
    if routed_path in {"handoff", "hybrid"}:
        print()
        print("Sponsor handoff:")
        print(f"- Booking URL: {sponsor_booking_url}")
        print(f"- Intake email: {sponsor_intake_email}")
        print("- Delivery: manual only. Email the generated analysis file to the intake address and Seren will forward it to Tom.")
    if send_result.get("status") == "instructions":
        print()
        print("Report sending instructions:")
        print(f"- To: {send_result['to']}")
        print(f"- Subject: {send_result['subject']}")
        print(f"- Action: {send_result['message']}")
    if answers.scam_check.exposed:
        print()
        print("Scam checkpoint: prior outreach already triggered one or more red flags.")
    if btcrecover_result is not None:
        print()
        print("Local btcrecover execution:")
        print(f"- Status: {btcrecover_result['status']}")
        print(f"- Return code: {btcrecover_result.get('returncode', 'n/a')}")
    if hashcat_result is not None:
        print()
        print("Local hashcat execution:")
        print(f"- Status: {hashcat_result['status']}")
        print(f"- Return code: {hashcat_result.get('returncode', 'n/a')}")
    print()
    for line in disclaimer_lines():
        print(line)
    print()
    for line in anti_scam_lines():
        print(line)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    payload = load_json(args.answers_file)
    answers = answers_from_json(payload) if payload else collect_answers_interactive()
    diagnosis = classify(answers)
    technical_user = likely_technical_user(answers, config)
    config.setdefault("inputs", {})
    if effective_path(diagnosis, technical_user) == "handoff":
        config["inputs"]["share_report"] = bool(config["inputs"].get("share_report", True))
    report = build_report(answers, diagnosis, technical_user)
    report_markdown = render_report_markdown(report)

    if args.report_out:
        write_report(report, args.report_out)

    send_result = {"status": "skipped", "reason": "dry run"}
    if not dry_run:
        send_result = maybe_send_report(
            report=report,
            report_markdown=report_markdown,
            config=config,
            send_report=args.send_report,
        )
    btcrecover_result: dict[str, Any] | None = None
    hashcat_result: dict[str, Any] | None = None
    if args.run_btcrecover:
        if dry_run:
            btcrecover_result = {"status": "blocked", "reason": "dry_run is true"}
        else:
            try:
                btcrecover_result = run_btcrecover(
                    config=config,
                    diagnosis=diagnosis,
                    technical_user=technical_user,
                )
            except BtcrecoverSafetyError as exc:
                btcrecover_result = {"status": "blocked", "reason": str(exc)}
    if args.run_hashcat:
        if dry_run:
            hashcat_result = {"status": "blocked", "reason": "dry_run is true"}
        else:
            try:
                hashcat_result = run_hashcat(
                    config=config,
                    diagnosis=diagnosis,
                    technical_user=technical_user,
                )
            except HashcatSafetyError as exc:
                hashcat_result = {"status": "blocked", "reason": str(exc)}

    print_result(
        answers=answers,
        diagnosis=diagnosis,
        technical_user=technical_user,
        config=config,
        send_result=send_result,
        btcrecover_result=btcrecover_result,
        hashcat_result=hashcat_result,
        report_path=args.report_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
