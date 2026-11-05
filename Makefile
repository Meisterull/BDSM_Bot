# Bequeme Targets + Test-Gate vor dem Deploy.
.PHONY: test build up down deploy logs backup

# Lokaler Test-Lauf (läuft mit MagicMock-Stubs auch ohne Docker/Deps).
# tests/run_alle.py findet ALLE tests/test_*.py selbst – nichts nachtragen.
test:
	python3 tests/run_alle.py

# Image bauen – nur wenn die Tests grün sind.
build: test
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

# Test -> Build -> Start. Kein Deploy mit roten Tests.
deploy: test build up
	@echo "✅ Deployed (Tests grün, Image gebaut, Container gestartet)."

logs:
	docker compose logs -f

backup:
	docker exec bdsm-bot python -m bot.services.backup
