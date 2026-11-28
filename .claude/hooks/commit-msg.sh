#!/bin/sh
# Git commit-msg hook: stamps the commit with the trailer `Dot-Hooks: commit-msg`, so that a commit made without the
# repository's hooks (`git -c core.hooksPath=/dev/null commit`, `git commit --no-verify`) shows it after the fact: the
# pre-push hook refuses to push one, and the gate fails a pull request that holds one (scripts/hook_stamps.py, item
# 2100). A stamp is no proof against forging, only against skipping the hooks unseen.
#
# Install once, as a copy (it reads nothing of the checkout, so every worktree, of any commit, is stamped the same):
#   cp .claude/hooks/commit-msg.sh "$(git rev-parse --git-path hooks)/commit-msg"
#
# A message with nothing but comments and blank lines is left alone, so that git still aborts the empty commit.
set -eu
if [ -z "$(git stripspace --strip-comments <"$1")" ]; then
  exit 0
fi
exec git interpret-trailers --in-place --if-exists addIfDifferent --trailer "Dot-Hooks: commit-msg" "$1"
