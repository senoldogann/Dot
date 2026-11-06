#!/bin/sh
# Writes the DOT_HOST build setting into the built app's Info.plist as its only WKAppBoundDomains entry.
#
# WKAppBoundDomains must name the host at build time, and the owner types the host at run time, so the key is left out
# unless DOT_HOST is set. With the key absent the app enforces the single origin itself (Navigation.swift); with it set,
# WebKit enforces it as well (limitsNavigationsToAppBoundDomains). An empty entry would mark every page as not
# app-bound and disable the bridge, which is why the key is added here rather than written into project.yml.
set -eu

plist="${TARGET_BUILD_DIR}/${INFOPLIST_PATH}"
stamp="${DERIVED_FILE_DIR}/app_bound_domains.stamp"
mkdir -p "${DERIVED_FILE_DIR}"

/usr/libexec/PlistBuddy -c "Delete :WKAppBoundDomains" "${plist}" 2>/dev/null || true
if [ -n "${DOT_HOST:-}" ]; then
  case "${DOT_HOST}" in
    *.ts.net) ;;
    *)
      echo "error: DOT_HOST must be a tailnet host ending in .ts.net, got '${DOT_HOST}'" >&2
      exit 1
      ;;
  esac
  /usr/libexec/PlistBuddy -c "Add :WKAppBoundDomains array" -c "Add :WKAppBoundDomains:0 string ${DOT_HOST}" "${plist}"
fi
echo "${DOT_HOST:-}" > "${stamp}"
