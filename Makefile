# Top-level Makefile for kerf
# Builds the C init binary and copies it to package data

all: init

init:
	$(MAKE) -C src/init
	mkdir -p src/kerf/data
	cp src/init/kerf-init src/kerf/data/

clean:
	$(MAKE) -C src/init clean
	rm -f src/kerf/data/kerf-init

.PHONY: all init clean
