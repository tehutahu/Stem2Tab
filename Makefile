.PHONY: dev-gpu dev-cpu test-backend test-frontend test-all setup-basicpitch

dev-gpu:
	docker compose up --build

dev-cpu:
	docker compose -f docker-compose.cpu.yml up --build

setup-basicpitch:
	cd backend && uv pip install --no-deps basic-pitch==0.4.0

test-backend:
	docker compose exec api uv run pytest tests/unit -q
	docker compose exec api uv run pytest tests/integration -q

test-frontend:
	docker compose exec -e NODE_ENV=test web npm test -- --run
	docker compose exec web npm run build

test-all: test-backend test-frontend

