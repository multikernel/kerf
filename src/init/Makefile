# Makefile for kerf-init
# Builds statically-linked binaries using musl-gcc

CC = musl-gcc
CFLAGS = -Wall -Wextra -Werror -O2 -static

all: kerf-init session-test

kerf-init: init.c session.c session.h proto.h
	$(CC) $(CFLAGS) -o $@ init.c session.c

# Runs one exec session on fd 0 for tests/test_exec_session.py; not shipped.
session-test: session_test.c session.c session.h proto.h
	$(CC) $(CFLAGS) -o $@ session_test.c session.c

clean:
	rm -f kerf-init session-test

.PHONY: all clean
