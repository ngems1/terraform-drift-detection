# Re-record with `make demo-gif` (vhs in Docker). Uses the real plans from the Moto e2e test.
Output docs/images/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1200
Set Height 620
Set Padding 16
Set Theme "Dracula"
Set TypingSpeed 22ms

Hide
Type "export PS1='$ ' PYTHONPATH=src; alias driftwatch='python3 -m driftwatch'; P=tests/fixtures/plans; C=tests/e2e/drift.toml; clear" Enter
Show

Type "driftwatch report --stack app-storage/prod --plan $P/drift.json --config $C --out /tmp/prod.json" Enter
Sleep 3.5s
Type "driftwatch report --stack app-storage/staging --plan $P/pending.json --config $C --out /tmp/stg.json" Enter
Sleep 2.5s
Type "clear; driftwatch notify /tmp/prod.json /tmp/stg.json --slack-webhook-env SLACK_WEBHOOK_URL --dry-run | head -24" Enter
Sleep 6s
