.PHONY: up down logs migrate seed test test-integration lint web-build prune-snapshots rollup-daily backfill

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:
	docker compose run --rm migrator alembic -c /app/packages/shared-py/alembic.ini upgrade head

seed:
	docker compose run --rm migrator python -m vxg_shared.db.seed

test:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

lint:
	ruff check packages apps/api apps/worker tests
	mypy packages/shared-py/src apps/api/src apps/worker/src tests

web-build:
	pnpm --dir apps/web lint && pnpm --dir apps/web typecheck && pnpm --dir apps/web build

prune-snapshots:
	docker compose exec worker python -m vxg_worker.cli prune-snapshots

rollup-daily:
	docker compose exec worker python -m vxg_worker.cli rollup-daily

backfill:
	docker compose exec worker python -m vxg_worker.cli backfill-history --domain ALL --months 24
