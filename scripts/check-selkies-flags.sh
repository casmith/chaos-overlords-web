#!/bin/sh
# Fail the build if Selkies has dropped a flag this project passes it.
#
# The base image is tracked by tag rather than digest. Pinning a digest was
# tried and is worse than it sounds: the tag is a rolling build of upstream's
# main branch, and upstream garbage-collects the manifests nothing points at,
# so a pin does not buy reproducibility -- it buys a build that works until
# upstream tidies up and then fails with "not found". That happened twice in
# two days. Tracking the tag never breaks that way, and this check covers what
# the pin was really protecting against: the Selkies inside changing under us.
#
# Only the flag *names* are checked. Whether a value still means the same thing
# is not something a build can know -- VIDEO_FULLCOLOR shipped as a working
# flag and still broke two browsers.
set -eu

run_script="$1"
help_output="$(selkies --help 2>&1)"

missing=""
for flag in $(grep -oE '^[[:space:]]+--[a-z0-9-]+' "${run_script}" | tr -d ' ' | sort -u); do
    case "${help_output}" in
        *"${flag}"*) ;;
        *) missing="${missing} ${flag}" ;;
    esac
done

if [ -n "${missing}" ]; then
    echo "ERROR: Selkies no longer accepts:${missing}" >&2
    echo "The base image has moved on. Check what replaced them before building." >&2
    exit 1
fi
echo "selkies accepts every flag this image passes"
