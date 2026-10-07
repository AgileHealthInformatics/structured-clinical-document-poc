.PHONY: install test lint run fixtures validate-ips validate-pdfa docker demo

install:            ## create a dev environment
	python -m pip install -e ".[dev]"

lint:
	ruff check src tests

test:               ## run unit, integration and acceptance tests
	pytest

run:                ## run the demonstrator on http://localhost:8080
	scdpoc serve --port 8080

fixtures:           ## deterministic IPS + PDF/A outputs for every fixture
	scdpoc build-fixtures --out build/fixtures

validate-ips: fixtures   ## HL7 FHIR validator against the pinned IPS package (needs Java)
	scripts/validate-ips.sh build/fixtures/*.ips.json

validate-pdfa: fixtures  ## veraPDF PDF/A-3b validation (needs veraPDF)
	scripts/validate-pdfa.sh build/fixtures/*.pdf

docker:
	docker compose up --build

demo:               ## scripted walkthrough against a running instance
	scripts/demo.sh http://localhost:8080
