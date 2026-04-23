ThreatLens frontend OS package legal bundle

This directory is generated from the Alpine runtime image shipped by web/Dockerfile.

Each package directory contains:
- APK-INFO: the raw package metadata block copied from /lib/apk/db/installed
- licenses/: copied files from /usr/share/licenses/<package>/ when that package publishes them in the runtime image

Alpine packages do not consistently ship full license texts for every installed package.
When a package only publishes metadata in the apk database, APK-INFO is the most specific
redistributed source record preserved by the built ThreatLens web image.
