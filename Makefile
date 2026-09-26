.PHONY: all test release clean release-sample

all test release clean:
	$(MAKE) -C pipeline $@

# Range-fetch a zhwiki multistream prefix (not the full pages-articles dump), then release.
release-sample:
	python3 -m importers.fetch_zhwiki_sample --output build/zhwiki-sample/sample.xml
	python3 -m importers.wikimedia --xml build/zhwiki-sample/sample.xml --output build/zhwiki-sample/sentences.txt
	$(MAKE) -C pipeline release CORPUS=$(abspath build/zhwiki-sample/sentences.txt)
