PYTHON := $(shell if [ -x .venv/bin/python ]; then printf .venv/bin/python; else printf python; fi)

test:
	$(PYTHON) -m pytest -q

test-web:
	node --check app.js
	node web-tests.mjs
