# Every target mirrors a CI job: green here = green in CI.
#   make all     lint + test + tf + security + docs     (no Docker needed)
#   make e2e     the whole pipeline against Moto        (Docker, ~2 min)
SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
UV ?= uv
HUB_RAW := https://raw.githubusercontent.com/ngems1/code-clinic/v1
IMAGE := ghcr.io/ngems1/code-clinic-devcontainer:1.1.0
# renovate: datasource=docker depName=motoserver/moto
MOTO_IMAGE := motoserver/moto:5.2.5
NET := drift-e2e
STACK := stacks/app-storage

.PHONY: help setup lint test tf e2e lab-shell lab-down fixtures security docs demo demo-gif ci-local all

help:      ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup:     ## install dev dependencies and git hooks
	$(UV) sync --locked
	pre-commit install

lint:      ## ruff, mypy, shellcheck, actionlint (CI: python-*, shell, repo)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy
	shellcheck -x scripts/*.sh tests/e2e/*.sh
	actionlint

test:      ## pytest (real Moto plans + synthetic) and bats (detect-drift.sh exit paths)
	$(UV) run pytest
	bats tests/bats

tf:        ## fmt, validate, tflint, terraform test (mock provider), checkov (CI: terraform)
	terraform fmt -check -recursive -diff stacks
	cd $(STACK) && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test
	cd $(STACK) && tflint --init >/dev/null && tflint --format compact
	cd $(STACK) && checkov -d . --framework terraform --compact --quiet

e2e:       ## apply, drift it out of band, detect, notify a fake Slack/Teams: all on Moto (Docker)
	docker network create $(NET) >/dev/null 2>&1 || true
	docker rm -f drift-moto >/dev/null 2>&1 || true
	docker run -d --name drift-moto --network $(NET) $(MOTO_IMAGE) >/dev/null
	docker run --rm --network $(NET) -e AWS_ENDPOINT=http://drift-moto:5000 \
	  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -v "$(CURDIR):/work" -w /work $(IMAGE) bash tests/e2e/run.sh \
	  || { docker rm -f drift-moto >/dev/null; exit 1; }
	docker rm -f drift-moto >/dev/null

lab-shell: ## Moto + a shell with tests/e2e/lab.sh loaded, for the drills (Docker)
	docker network create $(NET) >/dev/null 2>&1 || true
	docker start drift-lab-moto >/dev/null 2>&1 || docker run -d --name drift-lab-moto --network $(NET) $(MOTO_IMAGE) >/dev/null
	docker run --rm -it --network $(NET) -e AWS_ENDPOINT=http://drift-lab-moto:5000 \
	  -e UV_PROJECT_ENVIRONMENT=/tmp/venv -v "$(CURDIR):/work" -w /work $(IMAGE) \
	  bash --rcfile tests/e2e/lab.sh

lab-down:  ## stop the drill lab's Moto (all its "AWS" resources vanish with it)
	docker rm -f drift-lab-moto >/dev/null 2>&1 || true
	rm -rf .lab

fixtures:  e2e ## re-generate tests/fixtures/plans/ from the e2e run (review the diff!)
	$(UV) run python scripts/save-fixtures.py

security:  ## gitleaks: files + this branch's history (CI: repo)
	gitleaks git --redact --no-banner --log-opts=HEAD .
	gitleaks dir --redact --no-banner .

docs:      ## course self-check: README structure, links, <details>, SVG, encoding (CI: repo)
	@if [ -f ../code-clinic/scripts/check_repo.py ]; then \
	  python3 ../code-clinic/scripts/check_repo.py --profile project; \
	else \
	  tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	  curl -fsSL "$(HUB_RAW)/scripts/check_repo.py" -o "$$tmp"; \
	  python3 "$$tmp" --profile project; \
	fi

demo:      ## the three real plans as drift reports, then the Slack payload of the drifted one
	@for p in clean drift pending; do \
	  $(UV) run driftwatch report --stack app-storage/$$p --plan tests/fixtures/plans/$$p.json \
	    --config tests/e2e/drift.toml --out /tmp/driftwatch-$$p.json; done
	-$(UV) run driftwatch notify /tmp/driftwatch-drift.json --slack-webhook-env SLACK_WEBHOOK_URL --dry-run

demo-gif:  ## re-record docs/images/demo.gif with vhs (Docker)
	docker run --rm -v "$(CURDIR):/vhs" --entrypoint bash ghcr.io/charmbracelet/vhs:v0.12.1 -c "vhs docs/images/demo.tape"

ci-local:  ## run .github/workflows/ci.yml with act on a clean clone (Docker + ../code-clinic)
	docker run --rm --user root -e REPO=$(notdir $(CURDIR)) -v /var/run/docker.sock:/var/run/docker.sock \
	  -v act-cache:/cache -v "$(CURDIR):/src:ro" -v "$(CURDIR)/../code-clinic:/hub:ro" \
	  $(IMAGE) bash /hub/tools/act-local.sh

all: lint test tf security docs
