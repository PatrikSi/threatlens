#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <packages-out> <metadata-out> <legal-output-dir>" >&2
  exit 1
fi

packages_out=$1
metadata_out=$2
legal_output_dir=$3

mkdir -p "$(dirname "$packages_out")" "$(dirname "$metadata_out")" "$legal_output_dir"
rm -rf "$legal_output_dir"/*

{
  printf '%s\n' '# ThreatLens frontend OS package inventory' '# Generated from /lib/apk/db/installed' ''
  awk 'BEGIN { RS=""; FS="\n" } { package=""; version=""; for (i=1; i<=NF; i++) { if ($i ~ /^P:/) package=substr($i, 3); else if ($i ~ /^V:/) version=substr($i, 3); } if (package != "" && version != "") print package "=" version; }' /lib/apk/db/installed | sort
  printf '\n'
} > "$packages_out"

{
  printf '%s\n' '# ThreatLens frontend OS package metadata' '# Generated from /lib/apk/db/installed' ''
  printf '%s\n' 'package\tversion\tlicense\torigin\thomepage'
  awk 'BEGIN { RS=""; FS="\n" } { package=""; version=""; license=""; origin=""; homepage=""; for (i=1; i<=NF; i++) { if ($i ~ /^P:/) package=substr($i, 3); else if ($i ~ /^V:/) version=substr($i, 3); else if ($i ~ /^L:/) license=substr($i, 3); else if ($i ~ /^o:/) origin=substr($i, 3); else if ($i ~ /^U:/) homepage=substr($i, 3); } if (package != "") printf "%s\t%s\t%s\t%s\t%s\n", package, version, license, origin, homepage; }' /lib/apk/db/installed | sort
  printf '\n'
} > "$metadata_out"

cat > "$legal_output_dir/README.txt" <<'EOF'
ThreatLens frontend OS package legal bundle

This directory is generated from the Alpine runtime image shipped by web/Dockerfile.

Each package directory contains:
- APK-INFO: the raw package metadata block copied from /lib/apk/db/installed
- licenses/: copied files from /usr/share/licenses/<package>/ when that package publishes them in the runtime image

Alpine packages do not consistently ship full license texts for every installed package.
When a package only publishes metadata in the apk database, APK-INFO is the most specific
redistributed source record preserved by the built ThreatLens web image.
EOF

awk -v out="$legal_output_dir" '
BEGIN { RS=""; FS="\n" }
{
  package=""
  for (i=1; i<=NF; i++) {
    if ($i ~ /^P:/) {
      package=substr($i, 3)
    }
  }
  if (package != "") {
    dir=out "/" package
    system("mkdir -p \"" dir "\"")
    print $0 > (dir "/APK-INFO")
    close(dir "/APK-INFO")
  }
}
' /lib/apk/db/installed

if [ -d /usr/share/licenses ]; then
  for source_dir in /usr/share/licenses/*; do
    [ -d "$source_dir" ] || continue
    package=$(basename "$source_dir")
    target_dir="$legal_output_dir/$package/licenses"
    mkdir -p "$target_dir"
    cp -R "$source_dir"/. "$target_dir"/
  done
fi
