#!/usr/bin/env bash
#
# Quick setup for Kuza-v2 - adds to PATH
#
# Run this if you've already installed dependencies
# and just need to make kuza2 available system-wide.
#

CODEY_V2_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine shell config
if [ -n "$BASH_VERSION" ]; then
    SHELL_CONFIG="$HOME/.bashrc"
elif [ -n "$ZSH_VERSION" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
else
    SHELL_CONFIG="$HOME/.bashrc"
fi

# Make scripts executable
chmod +x "$CODEY_V2_DIR/kuza2"
chmod +x "$CODEY_V2_DIR/kuzad2"

# Add to PATH if not already there
if ! grep -q "kuza-v2" "$SHELL_CONFIG" 2>/dev/null; then
    echo "" >> "$SHELL_CONFIG"
    echo "# Kuza-v2" >> "$SHELL_CONFIG"
    echo "export PATH=\"$CODEY_V2_DIR:\$PATH\"" >> "$SHELL_CONFIG"
    echo "Added kuza2 to PATH in $SHELL_CONFIG"
else
    echo "kuza2 already in PATH"
fi

# Source the config
source "$SHELL_CONFIG"

# Create daemon directory
mkdir -p "$HOME/.kuza-v2"

echo ""
echo "Setup complete!"
echo ""
echo "Now you can use Kuza-v2:"
echo "  kuzad2 start          # Start the daemon"
echo "  kuza2 \"hello\"         # Send a task"
echo "  kuza2 status          # Check status"
echo ""
