# Makefile for kerf-init
# Builds a statically-linked init binary using musl-gcc

CC = musl-gcc
CFLAGS = -Wall -Wextra -Werror -O2 -static
TARGET = kerf-init

all: $(TARGET)

$(TARGET): init.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)

.PHONY: all clean
