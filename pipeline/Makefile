.PHONY: all test release clean

ROOT := $(abspath ..)
export PYTHONPATH := $(ROOT)
PYTHON ?= python3
RUN := $(PYTHON) -m pipeline.run

WORK ?= build/fixture
MAX_KEYS ?= 4
DICT_FULL ?= testdata/dict.full
DICT_HEAD ?= $(ROOT)/lexicon/dict_head.utf8
XML ?= testdata/wiki.xml
RELEASE_MAX_KEYS ?= 2000000

all:
	$(RUN) all --xml $(XML) --dict-full $(DICT_FULL) --dict-head $(DICT_HEAD) \
		--work $(WORK) --max-keys $(MAX_KEYS) --surface \
		--cut 0,0,0 --discount ABS,0.5 --discount ABS,0.5 --discount ABS,0.5

test:
	$(PYTHON) $(ROOT)/pipeline/test_pipeline.py

release:
	@test -n "$(DUMP)" || { echo "DUMP= pages-articles dump (optionally more paths)"; exit 1; }
	$(RUN) all $(foreach d,$(DUMP),--xml $(d)) \
		--dict-full $(ROOT)/data/dict.full --dict-head $(DICT_HEAD) \
		--work build/release --max-keys $(RELEASE_MAX_KEYS) --surface

clean:
	rm -rf build
