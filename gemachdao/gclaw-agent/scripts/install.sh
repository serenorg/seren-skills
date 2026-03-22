#!/usr/bin/env bash
# install.sh — Install the Gclaw binary
# Usage: bash scripts/install.sh [--version <tag>]
#
# By default, downloads and runs the canonical installer from the Gclaw repo.
# Falls back to a local release download when the upstream script is unavailable.
set -euo pipefail

REPO="GemachDAO/Gclaw"
INSTALL_DIR="${GCLAW_INSTALL_DIR:-${HOME}/.local/bin}"
BINARY_NAME="gclaw"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ─── Parse args ──────────────────────────────────────────────────────────────
VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        die "Missing value for --version. Usage: bash scripts/install.sh --version <tag>"
      fi
      VERSION="$2"
      shift 2
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

# ─── Try canonical upstream installer first ───────────────────────────────────
UPSTREAM_URL="https://raw.githubusercontent.com/${REPO}/main/install.sh"

if [[ -z "$VERSION" ]]; then
  info "Running canonical installer from ${REPO}..."
  if command -v curl &>/dev/null; then
    if curl -fsSL "$UPSTREAM_URL" -o /tmp/gclaw-install.sh 2>/dev/null; then
      bash /tmp/gclaw-install.sh
      rm -f /tmp/gclaw-install.sh
      exit $?
    fi
  elif command -v wget &>/dev/null; then
    if wget -qO /tmp/gclaw-install.sh "$UPSTREAM_URL" 2>/dev/null; then
      bash /tmp/gclaw-install.sh
      rm -f /tmp/gclaw-install.sh
      exit $?
    fi
  fi
  warn "Could not fetch upstream installer — falling back to local release download"
fi

# ─── Fallback: local release download ─────────────────────────────────────────

# Detect OS and architecture
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
  linux)  OS_NAME="Linux" ;;
  darwin) OS_NAME="Darwin" ;;
  *)      die "Unsupported OS: $OS. Please build from source: https://github.com/${REPO}" ;;
esac

case "$ARCH" in
  x86_64|amd64)  ARCH_NAME="x86_64" ;;
  aarch64|arm64) ARCH_NAME="arm64" ;;
  armv7l)        ARCH_NAME="armv7" ;;
  riscv64)       ARCH_NAME="riscv64" ;;
  *)             die "Unsupported architecture: $ARCH. Please build from source: https://github.com/${REPO}" ;;
esac

info "Detected platform: ${OS_NAME}/${ARCH_NAME}"

# Resolve version
if [[ -z "$VERSION" ]]; then
  info "Fetching latest release from GitHub..."
  if command -v curl &>/dev/null; then
    VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": "\(.*\)".*/\1/')
  elif command -v wget &>/dev/null; then
    VERSION=$(wget -qO- "https://api.github.com/repos/${REPO}/releases/latest" \
      | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": "\(.*\)".*/\1/')
  else
    die "curl or wget is required to download Gclaw"
  fi
  [[ -z "$VERSION" ]] && die "Could not determine latest release version. Try: bash scripts/install.sh --version <tag>"
fi

info "Installing Gclaw ${VERSION} for ${OS_NAME}/${ARCH_NAME}..."

# Build download URL
TARBALL="${BINARY_NAME}_${OS_NAME}_${ARCH_NAME}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${TARBALL}"

# Download
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

CHECKSUMS_FILE="checksums.txt"
CHECKSUMS_URL="https://github.com/${REPO}/releases/download/${VERSION}/${CHECKSUMS_FILE}"

info "Downloading ${DOWNLOAD_URL}..."
if command -v curl &>/dev/null; then
  curl -fsSL "$DOWNLOAD_URL" -o "${TMP_DIR}/${TARBALL}" \
    || die "Download failed. Check that release ${VERSION} exists at https://github.com/${REPO}/releases"
  curl -fsSL "$CHECKSUMS_URL" -o "${TMP_DIR}/${CHECKSUMS_FILE}" 2>/dev/null || true
else
  wget -q "$DOWNLOAD_URL" -O "${TMP_DIR}/${TARBALL}" \
    || die "Download failed. Check that release ${VERSION} exists at https://github.com/${REPO}/releases"
  wget -q "$CHECKSUMS_URL" -O "${TMP_DIR}/${CHECKSUMS_FILE}" 2>/dev/null || true
fi

# Verify checksum
if [[ -s "${TMP_DIR}/${CHECKSUMS_FILE}" ]]; then
  info "Verifying SHA256 checksum..."
  EXPECTED_SUM=$(grep "${TARBALL}" "${TMP_DIR}/${CHECKSUMS_FILE}" | awk '{print $1}')
  if [[ -z "$EXPECTED_SUM" ]]; then
    warn "Checksum entry for ${TARBALL} not found in ${CHECKSUMS_FILE} — skipping verification"
  else
    if command -v sha256sum &>/dev/null; then
      ACTUAL_SUM=$(sha256sum "${TMP_DIR}/${TARBALL}" | awk '{print $1}')
    elif command -v shasum &>/dev/null; then
      ACTUAL_SUM=$(shasum -a 256 "${TMP_DIR}/${TARBALL}" | awk '{print $1}')
    else
      warn "sha256sum/shasum not available — skipping checksum verification"
      ACTUAL_SUM="$EXPECTED_SUM"
    fi
    if [[ "$ACTUAL_SUM" != "$EXPECTED_SUM" ]]; then
      die "SHA256 checksum mismatch for ${TARBALL}!
  Expected: ${EXPECTED_SUM}
  Got:      ${ACTUAL_SUM}
Remove ${TMP_DIR} and retry, or verify the release at https://github.com/${REPO}/releases"
    fi
    success "SHA256 checksum verified"
  fi
else
  warn "No checksums file found for release ${VERSION} — skipping integrity verification"
fi

# Extract
info "Extracting archive..."
tar -xzf "${TMP_DIR}/${TARBALL}" -C "${TMP_DIR}" \
  || die "Failed to extract archive"

BINARY_PATH=$(find "${TMP_DIR}" -type f -name "${BINARY_NAME}" | head -1)
[[ -z "$BINARY_PATH" ]] && die "Could not find '${BINARY_NAME}' binary in extracted archive"

# Install
mkdir -p "$INSTALL_DIR"
chmod +x "$BINARY_PATH"
cp "$BINARY_PATH" "${INSTALL_DIR}/${BINARY_NAME}"

# Verify
if "${INSTALL_DIR}/${BINARY_NAME}" version &>/dev/null 2>&1; then
  INSTALLED_VERSION=$("${INSTALL_DIR}/${BINARY_NAME}" version 2>/dev/null | head -1 || echo "unknown")
  success "Gclaw installed successfully: ${INSTALLED_VERSION}"
else
  success "Gclaw installed to ${INSTALL_DIR}/${BINARY_NAME}"
fi

# Ensure PATH
if ! echo ":${PATH}:" | grep -q ":${INSTALL_DIR}:"; then
  warn "${INSTALL_DIR} is not in your PATH."
  echo "  Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
  echo ""
  echo "  export PATH=\"${INSTALL_DIR}:\${PATH}\""
  echo ""
fi

# Next steps
echo ""
echo -e "${GREEN}═══ Next Steps ═══${RESET}"
echo "  1. Run the interactive setup wizard:"
echo "       gclaw onboard"
echo ""
echo "  2. Start interactive agent:"
echo "       gclaw agent"
echo ""
echo "  3. Or start full gateway (web dashboard, channels, cron):"
echo "       gclaw gateway"
echo ""
echo "  Dashboard: http://127.0.0.1:18790/dashboard"
echo ""
echo "  Full documentation: https://github.com/${REPO}"
