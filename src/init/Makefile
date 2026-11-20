# Makefile for kerf-init
# Builds statically-linked binaries using musl-gcc

CC = musl-gcc
CFLAGS = -Wall -Wextra -Werror -O2 -static

all: kerf-init session-test lockdown-test

kerf-init: init.c session.c session.h proto.h lockdown.c lockdown.h btf.c btf.h bpf_uapi.h
	$(CC) $(CFLAGS) -o $@ init.c session.c lockdown.c btf.c

# Runs one exec session on fd 0 for tests/test_exec_session.py; not shipped.
session-test: session_test.c session.c session.h proto.h
	$(CC) $(CFLAGS) -o $@ session_test.c session.c

# Runs the lockdown code on a BTF file for tests/test_init_*.py; not shipped.
lockdown-test: lockdown_test.c lockdown.c lockdown.h btf.c btf.h bpf_uapi.h
	$(CC) $(CFLAGS) -o $@ lockdown_test.c lockdown.c btf.c

clean:
	rm -f kerf-init session-test lockdown-test

.PHONY: all clean
