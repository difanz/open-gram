.PHONY: all test fetch release clean

all test fetch release clean:
	$(MAKE) -C pipeline $@
