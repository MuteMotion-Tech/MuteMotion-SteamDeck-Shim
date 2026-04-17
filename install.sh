#!/bin/bash
# MuteMotion-Shim Quick Installer for Steam Deck
# Run this via: curl -sL https://raw.githubusercontent.com/MuteMotion-Tech/MuteMotion-SteamDeck-Shim/main/install.sh | bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=== MuteMotion Hardware Beta Installer ===${NC}"
echo "Fetching latest beta release from Github..."

# Define paths
PLUGIN_NAME="MuteMotion SteamDeck"
PLUGIN_DIR="$HOME/homebrew/plugins/$PLUGIN_NAME"
TEMP_DIR="/tmp/mutemotion_install"
DOWNLOAD_URL="https://github.com/MuteMotion-Tech/MuteMotion-SteamDeck-Shim/releases/latest/download/MuteMotion-SteamDeck.tar.gz"

# Cleanup previous temp installs
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"
cd "$TEMP_DIR"

# Download the tarball
echo "Downloading payload..."
if ! curl -sL "$DOWNLOAD_URL" -o mutemotion.tar.gz; then
    echo -e "${RED}Failed to download MuteMotion. Are you connected to the internet?${NC}"
    exit 1
fi

# Extract
echo "Extracting..."
tar -xzf mutemotion.tar.gz

# Inform user of sudo requirement
echo -e "${CYAN}We need root access to move the plugin into Decky Loader's directory.${NC}"
echo "Please enter your sudo password if prompted:"

# Remove old version if it exists
sudo rm -rf "$PLUGIN_DIR"

# Move the extracted folder to plugins
sudo mv "$PLUGIN_NAME" "$HOME/homebrew/plugins/"

# Fix permissions so Decky Loader owns it
sudo chown -R root:root "$PLUGIN_DIR"

# Restart Decky Loader to load the plugin
echo "Restarting Plugin Loader..."
sudo systemctl restart plugin_loader.service

echo -e "${GREEN}Installation Complete!${NC}"
echo "Open the Quick Access Menu (QAM) in Game Mode to activate MuteMotion."

# Cleanup
cd ~
rm -rf "$TEMP_DIR"
