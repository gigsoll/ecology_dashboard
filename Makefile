up:
	docker compose -f compose-dev.yml up -d
down:
	docker compose -f compose-dev.yml down -v
ps:
	docker compose -f compose-dev.yml ps
logs:
	docker compose -f compose-dev.yml logs $(filter-out $@,$(MAKECMDGOALS))
sh:
	docker compose -f compose-dev.yml exec -it $(filter-out $@,$(MAKECMDGOALS)) bash
build:
	docker compose -f compose-dev.yml up -d --build

