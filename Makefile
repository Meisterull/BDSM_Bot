# Bequeme Targets + Test-Gate vor dem Deploy.
.PHONY: test build up down deploy logs backup

# Lokaler Test-Lauf (läuft mit MagicMock-Stubs auch ohne Docker/Deps).
test:
	python3 tests/test_lern_system.py
	python3 tests/test_rollen.py
	python3 tests/test_locales.py
	python3 tests/test_presets.py
	python3 tests/test_spass.py
	python3 tests/test_paare.py
	python3 tests/test_review_d8.py
	python3 tests/test_review_d9.py
	python3 tests/test_sticker.py
	python3 tests/test_abwesenheit.py
	python3 tests/test_datum_erkennung.py
	python3 tests/test_lokal_llm.py
	python3 tests/test_stimmung.py

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
