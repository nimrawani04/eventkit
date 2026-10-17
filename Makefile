PYTHON := $(shell if [ -x .venv/bin/python ]; then printf .venv/bin/python; else printf python; fi)

test:
	$(PYTHON) -m pytest -q
