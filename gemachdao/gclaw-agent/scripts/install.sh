#!/usr/bin/env bash
# install.sh — Install the Gclaw binary from GitHub releases
# Usage: bash scripts/install.sh [--version <tag>]
set -euo pipefail

REPO="GemachDAO/Gclaw"
INSTALL_DIR="${GCLAW_INSTALL_DIR:-/usr/local/bin}"
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
usage()   { echo "Usage: bash scripts/install.sh [--version <tag>]"; exit 1; }

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

# ─── Check if already installed ──────────────────────────────────────────────
if command -v gclaw &>/dev/null; then
  CURRENT_VERSION=$(gclaw version 2>/dev/null | head -1 || echo "unknown")
  warn "gclaw is already installed: ${CURRENT_VERSION}"
  warn "Run with --version <tag> to install a specific version, or remove the existing binary first."
  exit 0
fi

# ─── Detect OS and architecture ──────────────────────────────────────────────
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
  linux)  OS_NAME="linux" ;;
  darwin) OS_NAME="darwin" ;;
  *)      die "Unsupported OS: $OS. Please build from source: https://github.com/${REPO}" ;;
esac

case "$ARCH" in
  x86_64|amd64)  ARCH_NAME="amd64" ;;
  aarch64|arm64) ARCH_NAME="arm64" ;;
  riscv64)       ARCH_NAME="riscv64" ;;
  *)             die "Unsupported architecture: $ARCH. Please build from source: https://github.com/${REPO}" ;;
esac

info "Detected platform: ${OS_NAME}/${ARCH_NAME}"

# ─── Resolve version ─────────────────────────────────────────────────────────
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

# ─── Build download URL ───────────────────────────────────────────────────────
# Expected asset pattern: gclaw_<version>_<os>_<arch>[.tar.gz]
# Strip leading 'v' from version for filename matching
VERSION_CLEAN="${VERSION#v}"
ASSET_NAME="gclaw_${VERSION_CLEAN}_${OS_NAME}_${ARCH_NAME}"
TARBALL="${ASSET_NAME}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${TARBALL}"

# ─── Download ─────────────────────────────────────────────────────────────────
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

info "Downloading ${DOWNLOAD_URL}..."
if command -v curl &>/dev/null; then
  curl -fsSL "$DOWNLOAD_URL" -o "${TMP_DIR}/${TARBALL}" \
    || die "Download failed. Check that release ${VERSION} exists at https://github.com/${REPO}/releases"
else
  wget -q "$DOWNLOAD_URL" -O "${TMP_DIR}/${TARBALL}" \
    || die "Download failed. Check that release ${VERSION} exists at https://github.com/${REPO}/releases"
fi

# ─── Extract ──────────────────────────────────────────────────────────────────
info "Extracting archive..."
tar -xzf "${TMP_DIR}/${TARBALL}" -C "${TMP_DIR}" \
  || die "Failed to extract archive"

# Find the binary (could be at root of archive or in a subdirectory)
BINARY_PATH=$(find "${TMP_DIR}" -type f -name "${BINARY_NAME}" | head -1)
[[ -z "$BINARY_PATH" ]] && die "Could not find '${BINARY_NAME}' binary in extracted archive"

# ─── Install ──────────────────────────────────────────────────────────────────
chmod +x "$BINARY_PATH"

if [[ -w "$INSTALL_DIR" ]]; then
  cp "$BINARY_PATH" "${INSTALL_DIR}/${BINARY_NAME}"
else
  info "Writing to ${INSTALL_DIR} requires elevated permissions..."
  sudo cp "$BINARY_PATH" "${INSTALL_DIR}/${BINARY_NAME}"
fi

# ─── Verify ───────────────────────────────────────────────────────────────────
if command -v gclaw &>/dev/null; then
  INSTALLED_VERSION=$(gclaw version 2>/dev/null | head -1 || echo "unknown")
  success "Gclaw installed successfully: ${INSTALLED_VERSION}"
else
  warn "Binary installed to ${INSTALL_DIR}/${BINARY_NAME} but 'gclaw' is not in PATH."
  warn "Add ${INSTALL_DIR} to your PATH or run: export PATH=\"\$PATH:${INSTALL_DIR}\""
fi

# ─── Next steps ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══ Next Steps ═══${RESET}"
echo "  1. Initialize your workspace:"
echo "       gclaw onboard"
echo ""
echo "  2. Configure API keys:"
echo "       cp .env.example .env"
echo "       # Edit .env with your LLM provider keys and GDEX trading keys"
echo ""
echo "  3. Start chatting with your agent:"
echo "       gclaw agent -m \"What is your GMAC balance?\""
echo ""
echo "  4. Start full gateway (web dashboard, channels, cron):"
echo "       gclaw gateway"
echo ""
echo "  Full documentation: https://github.com/${REPO}"
