#!/bin/sh
# Packs this machine's Dhan/Telegram credential files (~/.dhan) into a 600-mode archive for copying to the server:
#   sh deploy/pack_credentials.sh && scp ~/dhan_credentials.tgz user@server:~ && ssh user@server 'tar xzf dhan_credentials.tgz -C ~ && chmod 700 ~/.dhan && chmod 600 ~/.dhan/* && rm dhan_credentials.tgz'
# The archive holds the client id, PIN, TOTP secret, the current access token and the Telegram bot token. Delete it after copying.
set -e
[ -d "$HOME/.dhan" ] || { echo "no ~/.dhan on this machine"; exit 1; }
tar czf "$HOME/dhan_credentials.tgz" -C "$HOME" .dhan/credentials.json .dhan/access_token .dhan/telegram.json 2>/dev/null || tar czf "$HOME/dhan_credentials.tgz" -C "$HOME" .dhan
chmod 600 "$HOME/dhan_credentials.tgz"; echo "written $HOME/dhan_credentials.tgz (mode 600) — copy it to the server, extract into ~, then delete both copies"
