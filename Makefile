.PHONY: install install-dev test lint format clean

install:
	pip install -r requirements.txt

install-dev: install
	pip install pytest pytest-asyncio black ruff

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check sidechannel/
	black --check sidechannel/

format:
	black sidechannel/ tests/
	ruff check --fix sidechannel/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf htmlcov/ .coverage .mypy_cache/ .ruff_cache/ *.egg-info/
