PYTHON ?= python3
IMAGE ?= billgen:local

.PHONY: install assets test lint typecheck regression check build smoke-wheel image smoke-image
install:
	$(PYTHON) -m pip install -c constraints-ci.txt -e '.[dev]'
assets:
	$(PYTHON) scripts/assets.py hydrate
	$(PYTHON) scripts/assets.py check
test:
	$(PYTHON) -m pytest -o addopts='' -q
lint:
	$(PYTHON) -m ruff check .
typecheck:
	$(PYTHON) -m mypy
regression:
	$(PYTHON) tools/render_regression.py check
check: lint typecheck test regression
build:
	$(PYTHON) -m build --no-isolation
# Prepare an offline wheelhouse explicitly; smoke_install itself never uses an index.
smoke-wheel: build
	$(PYTHON) -m pip download -c constraints-ci.txt --dest dist/wheelhouse dist/billgen-*.whl
	$(PYTHON) tools/smoke_install.py --wheel dist/billgen-*.whl --wheelhouse dist/wheelhouse
image:
	docker build -t $(IMAGE) .
smoke-image: image
	$(PYTHON) tools/smoke_container.py --image $(IMAGE)
