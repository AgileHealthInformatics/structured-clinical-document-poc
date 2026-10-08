#!/usr/bin/env bash
# Unattended veraPDF CLI install for CI (IzPack automated installation).
# Usage: scripts/ci/install-verapdf.sh <install-dir>
set -euo pipefail
DEST="${1:-$HOME/verapdf}"
WORK=$(mktemp -d)
curl -fsSL -o "$WORK/verapdf-installer.zip" https://software.verapdf.org/releases/verapdf-installer.zip
unzip -q "$WORK/verapdf-installer.zip" -d "$WORK"
cat > "$WORK/auto-install.xml" <<XML
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
  <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
  <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
    <installpath>${DEST}</installpath>
  </com.izforge.izpack.panels.target.TargetPanel>
  <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
    <pack index="0" name="veraPDF Mac and *nix Scripts" selected="true"/>
    <pack index="1" name="veraPDF Validation model" selected="true"/>
    <pack index="2" name="veraPDF Documentation" selected="false"/>
    <pack index="3" name="veraPDF Sample Plugins" selected="false"/>
  </com.izforge.izpack.panels.packs.PacksPanel>
  <com.izforge.izpack.panels.install.InstallPanel id="install"/>
  <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
XML
INSTALLER=$(find "$WORK" -maxdepth 2 -name "verapdf-install" -type f | head -1)
"$INSTALLER" "$WORK/auto-install.xml"
"$DEST/verapdf" --version
