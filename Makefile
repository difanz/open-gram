.PHONY: all test release clean

all test release clean:
	$(MAKE) -C pipeline $@
