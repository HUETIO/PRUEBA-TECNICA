# Keep the target names and output contracts required by the evaluator.
.PHONY: setup up run stream front test down clean verificar

PYTHON ?= python3
ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif

setup:
	$(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements.txt

up:
	$(VENV_PY) tools/bootstrap.py
	docker compose up -d --wait
	$(VENV_PY) -m src.database

run:
	$(VENV_PY) -m src.batch

stream:
	$(VENV_PY) -m src.stream

front:
	$(VENV_PY) -m src.frontend

test:
	$(VENV_PY) -m unittest discover -s tests -v

down:
	docker compose down

clean:
	docker compose down -v
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('out', 'logs')]"

verificar:
	$(VENV_PY) tools/verificar_entrega.py
