prefix = docker compose -f compose-dev.yml

ARGS ?=

up:
	$(prefix) up -d $(ARGS)
down:
	$(prefix) down $(ARGS)
ps:
	$(prefix) ps
logs:
	$(prefix) logs $(ARGS)
sh:
	$(prefix) exec -it $(ARGS) bash
build:
	$(prefix) up -d --build $(ARGS)
exec:
	$(prefix) exec $(ARGS)
