# Convenience targets for the coverage dashboard. The scripts are
# canonical; these just save typing. Run from anywhere via
# `make -C coverage <target>`.
PYTHON ?= python3

.PHONY: regen check check-full test site publish-gh-pages
regen:          ## regenerate coverage/data/coverage.json
	./regen.sh

check:          ## fast freshness check (fingerprints only, seconds)
	$(PYTHON) check_freshness.py

check-full:     ## freshness check + full regeneration comparison (minutes)
	$(PYTHON) check_freshness.py --full

test:           ## fixture tests (pure logic, no pyang needed)
	$(PYTHON) -m unittest discover -s tests -v

site:           ## assemble the static site into $(OUT) (default _site)
	./build_site.sh $(or $(OUT),_site)

publish-gh-pages:  ## dry-run the gh-pages fallback publish (add --push via the script)
	./publish_gh_pages.sh
