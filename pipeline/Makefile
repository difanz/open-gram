.PHONY: all test release clean

ROOT := $(abspath ..)
export PYTHONPATH := $(ROOT)
PYTHON ?= python3
RUN := $(PYTHON) -m pipeline.run

WORK ?= build/fixture
MAX_KEYS ?= 4
DICT_FULL ?= testdata/dict.full
DICT_HEAD ?= $(ROOT)/lexicon/dict_head.utf8
FIXTURE_CORPUS ?= testdata/sentences.txt
RELEASE_MAX_KEYS ?= 2000000

all:
	$(RUN) all --corpus $(FIXTURE_CORPUS) --dict-full $(DICT_FULL) --dict-head $(DICT_HEAD) \
		--work $(WORK) --max-keys $(MAX_KEYS) --surface \
		--cut 0,0,0 --discount ABS,0.5 --discount ABS,0.5 --discount ABS,0.5

test:
	$(PYTHON) $(ROOT)/pipeline/test_pipeline.py
	$(PYTHON) $(ROOT)/importers/test_wikimedia.py
	$(PYTHON) $(ROOT)/importers/test_fetch_zhwiki_sample.py
	$(PYTHON) $(ROOT)/lexicon/test_merge_cedict.py

release:
	@test -n "$(CORPUS)" || { echo "CORPUS= sentence file is required"; exit 1; }
	$(RUN) all --corpus $(CORPUS) \
		--dict-full $(ROOT)/data/dict.full --dict-head $(DICT_HEAD) \
		--work build/release --max-keys $(RELEASE_MAX_KEYS) --surface

clean:
	rm -rf build
