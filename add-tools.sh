#!/bin/bash
set -xeuo pipefail

echo $(whoami)

git config --global alias.s "status -s"
fmt=""
# fmt+=" %C(red)%h"                   # short hash
fmt+=" %C(bold blue)<%an>"           # author
fmt+=" %C(cyan)%cd"                  # date
fmt+=" %C(green)(%cr)"              # relative time
fmt+=" %C(reset)"                   # reset
fmt+=" %s"                          # message
fmt+=" %C(yellow)%d"               # refs
git config --global alias.l "log --color --graph --pretty=format:'$fmt' --abbrev-commit --date=format:'%Y-%m-%d %H:%M:%S'"
git config --global alias.p "pull"
git config --global alias.ds "diff --stat HEAD"
git config --global alias.coma "commit -am"
git config --global merge.noEdit true
git config --global pull.rebase false

git config --list --global


al="alias l='ls -1XA'"
grep --quiet --line-regexp --fixed-strings "$al" ~/.bashrc ||
printf '\n%s\n' "$al" >> ~/.bashrc
hash -r




curl -LsSf https://astral.sh/uv/install.sh | sh
curl https://install.duckdb.org |  sh

cmd="export PATH='/home/$(whoami)/.duckdb/cli/1.5.4':\$PATH"
grep --quiet --line-regexp --fixed-strings "$cmd" ~/.bashrc ||
printf '\n%s\n' "$cmd" >> ~/.bashrc
hash -r


export PATH="/home/$(whoami)/.duckdb/cli/1.4.4:$PATH"
duckdb --version

code --install-extension analytic-signal.preview-pdf 2>/dev/null || true
code --install-extension ms-python.vscode-pylance 2>/dev/null || true



sudo apt-get update -qq 2>/dev/null || true
sudo apt-get install -qq -y libarchive-tools

exec bash
