# ============================================================================
#  Convenience wrapper around docker compose + the management scripts.
# ============================================================================
SHELL := /bin/bash

# Use "docker compose" (v2) if present, else fall back to docker-compose.
DC := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: build
build: ## Build the postfix and dovecot images
	$(DC) build

.PHONY: up
up: ## Start the whole stack in the background
	$(DC) up -d

.PHONY: down
down: ## Stop the stack (keeps volumes/data)
	$(DC) down

.PHONY: restart
restart: ## Restart all services
	$(DC) restart

.PHONY: reload
reload: ## Reload postfix, dovecot and rspamd config without downtime
	-$(DC) exec postfix postfix reload
	-$(DC) exec dovecot doveadm reload
	-$(DC) exec rspamd rspamadm control reload

.PHONY: logs
logs: ## Tail logs from all services (Ctrl-C to stop)
	$(DC) logs -f --tail=100

.PHONY: ps
ps: ## Show service status
	$(DC) ps

.PHONY: tls-self-signed
tls-self-signed: ## Generate a self-signed TLS cert (testing)
	./scripts/setup-tls.sh self-signed

.PHONY: tls-letsencrypt
tls-letsencrypt: ## Obtain a Let's Encrypt cert (needs port 80)
	./scripts/setup-tls.sh letsencrypt

.PHONY: add-domain
add-domain: ## Register a domain:           make add-domain DOMAIN=example.com
	./scripts/add-domain.sh $(DOMAIN)

.PHONY: add-user
add-user: ## Create a mailbox:              make add-user EMAIL=you@example.com [QUOTA=1024]
	./scripts/add-user.sh $(EMAIL) "" $(or $(QUOTA),0)

.PHONY: del-user
del-user: ## Delete a mailbox:              make del-user EMAIL=you@example.com
	./scripts/del-user.sh $(EMAIL)

.PHONY: add-alias
add-alias: ## Create an alias:              make add-alias SRC=info@example.com DST=you@example.com
	./scripts/add-alias.sh $(SRC) $(DST)

.PHONY: dkim
dkim: ## Generate a DKIM key + DNS record: make dkim DOMAIN=example.com
	./scripts/gen-dkim.sh $(DOMAIN)

.PHONY: list
list: ## List domains, mailboxes and aliases
	./scripts/list.sh
