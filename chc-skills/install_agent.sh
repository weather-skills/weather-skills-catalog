#!/usr/bin/env bash
# Install the CHC agent and skills as a Claude Code plugin.
set -e

claude plugin marketplace add rhiza-research/chc-skills
claude plugin install rhiza-chc@chc-skills

cat <<'EOF'

Installed the rhiza-chc plugin.

Next steps:

# Then run the agent
claude --agent rhiza-chc:chc

EOF
