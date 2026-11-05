# Makefile for the Job Hunter app.
#
# Always uses the venv's own Python ($(PY)) directly, so it is immune to the
# shell `python` alias and any PYTHONPATH set in ~/.zshrc (both have bitten this
# project before). `unset PYTHONPATH` is applied per-recipe for the same reason.
#
# Common usage:
#   make run        # set up the venv (first time) + start the app
#   make install    # (re)create the venv and install dependencies
#   make check      # verify the app imports cleanly
#   make clean      # remove the venv

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(PY) -m pip

# Marker file: lets `install` be a no-op when deps are already in sync with
# requirements.txt (the stamp is newer than requirements.txt).
STAMP := $(VENV)/.deps-installed

.DEFAULT_GOAL := run
.PHONY: run install check clean venv

# Create the virtualenv if it does not exist yet.
$(PY):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

venv: $(PY)

# Install/update dependencies. Re-runs only when requirements.txt changes.
$(STAMP): requirements.txt | $(PY)
	unset PYTHONPATH; $(PIP) install -r requirements.txt
	@touch $(STAMP)

install: $(STAMP)

# Start the app (ensures the venv + deps are ready first).
run: $(STAMP)
	unset PYTHONPATH; $(PY) run.py

# Quick sanity check that the app imports without starting the server.
check: $(STAMP)
	unset PYTHONPATH; $(PY) -c "from app import main; print('OK', len(main.app.routes), 'routes')"

# Remove the virtualenv (next `make run` recreates it).
clean:
	rm -rf $(VENV)
